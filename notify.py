import os
import logging
import requests
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def send_ntfy_alert(photo_path: str, title: str, message: str, click_url: str = None) -> bool:
    """
    Sends a native mobile push notification via ntfy.sh.
    The mobile app displays the title, message, and snapshot image on lock screen.
    """
    if not getattr(config, "NTFY_ENABLED", False):
        return False

    topic = getattr(config, "NTFY_TOPIC", "")
    server = getattr(config, "NTFY_SERVER", "https://ntfy.sh")
    if not topic:
        logger.warning("ntfy topic is not configured in config.py")
        return False

    url = f"{server.rstrip('/')}/{topic}"
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": "high",
        "Tags": "camera,bell,warning",
        "Filename": os.path.basename(photo_path),
        "Message": message.encode("utf-8")
    }

    if click_url:
        headers["Click"] = click_url

    try:
        with open(photo_path, "rb") as f:
            response = requests.post(url, data=f, headers=headers, timeout=10)
        
        if response.status_code == 200:
            logger.info("[ntfy] Push notification delivered to topic: %s", topic)
            return True
        else:
            logger.error("[ntfy] Failed to send notification: %s - %s", response.status_code, response.text)
            return False
    except Exception as e:
        logger.error("[ntfy] Exception while sending push alert: %s", e)
        return False

def send_telegram_photo(photo_path: str, caption: str) -> bool:
    """Sends photo notification via Telegram bot."""
    if not getattr(config, "TELEGRAM_ENABLED", False):
        return False

    if not config.TELEGRAM_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(photo_path, "rb") as photo_file:
            files = {"photo": photo_file}
            data = {
                "chat_id": config.TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data, files=files, timeout=10)
        return response.status_code == 200
    except Exception as e:
        logger.error("Telegram error: %s", e)
        return False

def dispatch_all_alerts(photo_path: str, recognized_name: str, trigger_source: str, timestamp_str: str):
    """Dispatches alerts to all enabled notification channels."""
    is_known = "Unknown" not in recognized_name and "Visitor" != recognized_name
    status_label = f"Known: {recognized_name}" if is_known else recognized_name
    
    title = f"Doorbell Alert: {status_label}"
    msg = f"Trigger: {trigger_source} motion at {timestamp_str}"
    
    click_url = f"http://{config.ESP32_CAM_IP}:81" if config.ESP32_CAM_IP else None

    # 1. Native CCTV Push (ntfy)
    send_ntfy_alert(photo_path, title, msg, click_url=click_url)

    # 2. Telegram (if enabled)
    if getattr(config, "TELEGRAM_ENABLED", False):
        caption = (
            f"🔔 <b>Doorbell Alert</b>\n"
            f"👤 <b>Status:</b> {status_label}\n"
            f"📍 <b>Trigger:</b> {trigger_source}\n"
            f"🕐 <b>Time:</b> {timestamp_str}"
        )
        send_telegram_photo(photo_path, caption)
