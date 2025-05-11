import time
from datetime import datetime
import uiautomator2 as u2
import logging
import re

# 配置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='upgrade_test.log'
)
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)

# 定义升级版本配置（1.0.21 ↔ 1.0.20 互刷）
UPGRADE_VERSIONS = {
    "1.0.21": {
        "target_version": "1.0.20",
        "update_file_name": "Xiaomi_M80P_1.0.20.img"
    },
    "1.0.20": {
        "target_version": "1.0.21",
        "update_file_name": "Xiaomi_M80P_1.0.21.img"
    }
}

# 版本别名映射（处理不规范初始版本）
VERSION_ALIASES = {
    "SUNWINON": "1.0.21"  # 可根据实际情况扩展
}


def wait_for_element(d, xpath=None, resourceId=None, text=None, timeout=10):
    """等待元素出现并返回元素对象，支持多种定位方式"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if xpath:
            element = d.xpath(xpath)
            if element.exists:
                return element
        elif resourceId and text:
            element = d(resourceId=resourceId, text=text)
        elif resourceId:
            element = d(resourceId=resourceId)
        elif text:
            element = d(text=text)
        else:
            raise ValueError("至少提供 xpath/resourceId/text 中的一个")

        if element.exists:
            return element
        time.sleep(0.5)
    raise TimeoutError(f"等待元素超时: xpath={xpath}, resourceId={resourceId}, text={text}")


def get_element_text(element):
    """安全获取元素文本，兼容新旧版本API"""
    try:
        return element.get_text() if hasattr(element, 'get_text') else element.text
    except Exception:
        return ""


def get_current_version(d):
    """优先通过 resourceId+text 精确获取版本号"""
    logging.info("开始获取当前设备版本号")

    # 精确匹配支持的版本（核心定位方式）
    for version in UPGRADE_VERSIONS.keys():
        try:
            element = wait_for_element(
                d,
                resourceId="com.dialog.suota:id/itemValue",
                text=version,
                timeout=3
            )
            return get_element_text(element).strip()
        except TimeoutError:
            continue  # 尝试下一个支持的版本

    # 兼容性方案：通过纯resourceId获取（处理文本不精确的情况）
    try:
        element = wait_for_element(d, resourceId="com.dialog.suota:id/itemValue")
        version = get_element_text(element).strip()
        if re.match(r'\d+\.\d+', version):  # 验证版本号格式
            return version
    except TimeoutError:
        pass

    # 层级定位方案（备用，适用于界面结构固定场景）
    try:
        # 定位到包含"版本"的布局（假设在mainItemsList的第4个条目）
        layout = wait_for_element(
            d,
            xpath='//*[@resource-id="com.dialog.suota:id/mainItemsList"]/android.widget.RelativeLayout[4]'
        )
        # 获取itemValue元素（版本号在第二个TextView）
        item_value = layout.child(className="android.widget.TextView").all()[1]
        return get_element_text(item_value).strip()
    except (TimeoutError, IndexError):
        pass

    # 最终方案：遍历所有itemValue元素（兜底）
    try:
        elements = d(resourceId="com.dialog.suota:id/itemValue").all()
        for elem in elements:
            text = get_element_text(elem)
            if text in UPGRADE_VERSIONS:
                return text
        raise ValueError("未找到支持的版本号")
    except TimeoutError:
        raise ValueError("无法获取设备版本号")


def select_update_file(d, current_version):
    """根据当前版本选择对应的升级文件（精确文件名匹配）"""
    config = UPGRADE_VERSIONS[current_version]
    file_name = config["update_file_name"]

    logging.info(f"当前版本 {current_version}，目标文件 {file_name}")

    # 进入文件列表界面
    wait_for_element(d, resourceId="com.dialog.suota:id/file_list", timeout=10).click()
    time.sleep(1)

    # 精确匹配文件名（核心操作）
    file_element = wait_for_element(
        d,
        resourceId="android:id/text1",
        text=file_name,
        timeout=10
    )
    file_element.click()
    return config["target_version"]


def perform_upgrade(d, current_version):
    """执行完整升级流程并验证结果"""
    try:
        target_version = UPGRADE_VERSIONS[current_version]["target_version"]

        # 1. 点击更新按钮
        wait_for_element(d, resourceId="com.dialog.suota:id/updateButton", timeout=10).click()
        time.sleep(1)

        # 2. 选择升级文件
        selected_version = select_update_file(d, current_version)

        # 3. 发送更新文件
        wait_for_element(
            d,
            resourceId="com.dialog.suota:id/sendToDeviceButton",
            text="SEND TO DEVICE",
            timeout=10
        ).click()

        # 4. 等待升级完成（强化等待逻辑）
        logging.info("等待升级完成（最长10分钟）")
        wait_for_element(d, text="Upload completed", timeout=600)  # 按实际界面修改
        wait_for_element(d, text="确定", timeout=60).click()  # 确认按钮
        time.sleep(5)

        # 5. 关闭升级界面并重新获取版本
        d.press("back")  # 返回主界面
        time.sleep(2)
        d = u2.connect()  # 重新连接确保界面刷新

        # 6. 验证版本号
        device_item = wait_for_element(
            d,
            xpath='//*[@resource-id="com.dialog.suota:id/device_list"]/android.widget.RelativeLayout[1]',
            timeout=15
        )
        device_item.click()
        time.sleep(2)
        new_version = get_current_version(d)

        return new_version == selected_version, new_version

    except TimeoutError as e:
        logging.error(f"升级步骤超时: {str(e)}")
        return False, current_version
    except Exception as e:
        logging.error(f"升级异常: {str(e)}")
        return False, current_version


def perform_update():
    """主流程：设备连接、版本检测、循环升级"""
    try:
        d = u2.connect()
        total_attempts = 0
        success_count = 0

        logging.info(f"{'=' * 30} 自动化升级测试 {'=' * 30}")

        # 设备连接与初始版本获取
        device_item = wait_for_element(
            d,
            xpath='//*[@resource-id="com.dialog.suota:id/device_list"]/android.widget.RelativeLayout[1]',
            timeout=20
        )
        device_item.click()
        time.sleep(3)

        current_version = get_current_version(d)
        if current_version in VERSION_ALIASES:
            current_version = VERSION_ALIASES[current_version]
            logging.info(f"映射初始版本: {current_version}")

        while True:
            total_attempts += 1
            logging.info(f"\n第 {total_attempts} 次尝试 | 当前版本: {current_version}")

            if current_version not in UPGRADE_VERSIONS:
                logging.error(f"不支持的版本: {current_version}，跳过")
                break  # 或根据需求处理

            success, new_version = perform_upgrade(d, current_version)

            if success:
                success_count += 1
                logging.info(f"✅ 升级成功 | 版本: {current_version} → {new_version}")
            else:
                # 保存失败截图
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                screenshot_path = f"screenshot_failure_{timestamp}.png"
                d.screenshot(screenshot_path)
                logging.error(f"❌ 升级失败 | 版本: {current_version} → {new_version} | 截图已保存")

            current_version = new_version
            logging.info(f"当前成功率: {success_count / total_attempts * 100:.2f}%")
            time.sleep(5)  # 控制测试频率

    except Exception as e:
        logging.critical(f"测试终止: {str(e)}")
        raise


if __name__ == "__main__":
    perform_update()
    #小米O88平板多版本1.0.20 → 1.0.21重复升级，已完成
    #遗留事项待加入时间戳，升级次数