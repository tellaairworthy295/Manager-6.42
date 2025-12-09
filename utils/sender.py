import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
import json

class EmailConfig:
    """
    Configuration holder for email sending with attachments, loaded from config.json.
    Supports receiving as a string or list.
    """
    def __init__(self, config_dict):
        self.SMTP_SERVER = config_dict.get("SMTP_SERVER", "smtp.qq.com")
        self.SMTP_PORT = config_dict.get("SMTP_PORT", 465)
        self.SENDER_EMAIL = config_dict.get("SENDER_EMAIL", "")
        self.SENDER_PASSWORD = config_dict.get("SENDER_PASSWORD", "")
        # Accepts either list or string, stores as list
        receiver = config_dict.get("RECEIVER_EMAIL", "")
        if isinstance(receiver, list):
            self.RECEIVER_EMAIL = receiver
        elif isinstance(receiver, str):
            if receiver.strip():
                self.RECEIVER_EMAIL = [r.strip() for r in receiver.split(",")]
            else:
                self.RECEIVER_EMAIL = []
        else:
            self.RECEIVER_EMAIL = []
        self.SUBJECT = config_dict.get("SUBJECT", "")
        self.BODY = config_dict.get("BODY", "")
        self.ATTACHMENTS = config_dict.get("ATTACHMENTS", [])

    def as_dict(self):
        """
        Return the configuration as a dictionary suitable for passing to send_email_with_attachments().
        """
        return {
            "sender_email": self.SENDER_EMAIL,
            "sender_password": self.SENDER_PASSWORD,
            "receiver_email": self.RECEIVER_EMAIL,
            "subject": self.SUBJECT,
            "body": self.BODY,
            "attachments": self.ATTACHMENTS,
            "smtp_server": self.SMTP_SERVER,
            "smtp_port": self.SMTP_PORT
        }

def load_email_config_from_json(json_path="json/config.json"):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    email_config_dict = data.get("EmailConfig", {})
    return EmailConfig(email_config_dict)

def send_email_with_attachments(
    sender_email: str,
    sender_password: str,
    receiver_email,
    subject: str,
    body: str,
    attachments: list,
    smtp_server: str = "smtp.qq.com",
    smtp_port: int = 465,
):
    """
    Send an email with multiple file attachments.
    Supports Excel (.xlsx, .xls) and image files (.png, .jpg, etc.)
    Args:
        sender_email: your email address
        sender_password: your app-specific password or SMTP auth code
        receiver_email: recipient address (str or list)
        subject: email subject line
        body: plain text email body
        attachments: list of file paths
        smtp_server: SMTP server hostname
        smtp_port: SMTP port (465 for TLS)
    """
    # --- Prepare recipient list ---
    if isinstance(receiver_email, str):
        receivers = [r.strip() for r in receiver_email.split(",") if r.strip()]
    elif isinstance(receiver_email, list):
        receivers = [r for r in receiver_email if r.strip()]
    else:
        receivers = []
    if not receivers:
        print("❌ No valid recipients specified.")
        return

    # --- Create email ---
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    # --- Add attachments ---
    for file_path in attachments:
        file_path = Path(file_path)
        if not file_path.exists():
            print(f"⚠️ File not found: {file_path}")
            continue

        with open(file_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={file_path.name}",
            )
            msg.attach(part)

    # --- Send via SMTP ---
    try:
        # For SSL (QQ Mail)
        server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        server.login(sender_email, sender_password)
        server.send_message(msg, from_addr=sender_email, to_addrs=receivers)
        server.quit()
        print(f"✅ Email sent successfully to: {', '.join(receivers)}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

if __name__ == "__main__":
    # Dynamically load configuration from config.json
    config = load_email_config_from_json("json/config.json")

    # Optionally override or update ATTACHMENTS dynamically here if needed, e.g.:
    # config.ATTACHMENTS = [
    #     "excel/2025-11-04.xlsx",
    #     "excel/trendings.xlsx",
    #     "excel/trendings_trend_break.png",
    #     "excel/trendings_trend_down.png",
    #     "excel/trendings_trend_even.png",
    #     "excel/trendings_trend_up.png"
    # ]

    send_email_with_attachments(**config.as_dict())