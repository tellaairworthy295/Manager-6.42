import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
import json
import time

# --- Import DB layer for accessing emails by user_id or globally ---
from utils.database import get_db_manager, UsersRepository
from utils.logging_config import get_others_logger
logger = get_others_logger()

class EmailConfig:
    """
    Configuration holder for email sending with attachments, loaded from config.json.
    Doesn’t handle recipients anymore (all in database), only SMTP/config info.
    """
    def __init__(self, config_dict):
        self.SMTP_SERVER = config_dict.get("SMTP_SERVER", "smtp.qq.com")
        self.SMTP_PORT = config_dict.get("SMTP_PORT", 465)
        self.SENDER_EMAIL = config_dict.get("SENDER_EMAIL", "")
        self.SENDER_PASSWORD = config_dict.get("SENDER_PASSWORD", "")
        self.SUBJECT = config_dict.get("SUBJECT", "")
        self.BODY = config_dict.get("BODY", "")
        self.ATTACHMENTS = config_dict.get("ATTACHMENTS", [])

    def as_dict(self):
        """
        Return the configuration as a dictionary suitable for passing to send_email_with_attachments(),
        except for receiver_email, which must now be provided at call time (from DB).
        """
        return {
            "sender_email": self.SENDER_EMAIL,
            "sender_password": self.SENDER_PASSWORD,
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

def get_receiver_emails_from_db(user_id=None):
    """
    Gets the recipient email(s) from the users database.
    - If user_id is provided: returns a singleton list [email] or empty list if not found/invalid.
    - If no user_id: returns a deduplicated list of all valid emails in users db.
    Uses get_db_manager() to acquire the db, following cookies_getter.py style.
    """
    db_manager = get_db_manager()
    user_repo = UsersRepository(db_manager)
    if user_id:
        user_email = user_repo.get_email_by_user_id(user_id)
        # Only return non-empty/valid emails (avoiding [None])
        return [user_email] if user_email else []
    else:
        # Return all unique/valid
        return user_repo.get_all_unique_valid_emails()
    
def send_email_with_attachments(
    sender_email: str,
    sender_password: str,
    subject: str = "",
    body: str = "",
    attachments: list = None,
    smtp_server: str = "smtp.qq.com",
    smtp_port: int = 465,
    user_id=None,
    receivers: list[str] | None = None,
):
    """
    Send an email with multiple file attachments.
    If receiver_email is not specified, it will be looked up from the database (optionally using user_id).
    Supports Excel (.xlsx, .xls) and image files (.png, .jpg, etc.)
    Args:
        sender_email: your email address
        sender_password: your app-specific password or SMTP auth code
        receiver_email: recipient address (str or list, or None to lookup from DB)
        subject: email subject line
        body: plain text email body
        attachments: list of file paths
        smtp_server: SMTP server hostname
        smtp_port: SMTP port (465 for TLS)
        user_id: if specified, used to lookup a single user email from DB
    """

    receivers = receivers if isinstance(receivers, list) else get_receiver_emails_from_db(user_id=user_id)

    if not receivers:
        return

    # --- Create email ---
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = ", ".join(receivers)
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    # --- Add attachments ---
    if attachments is None:
        attachments = []
    for file_path in attachments:
        file_path = Path(file_path)
        if not file_path.exists():
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

    # --- Send via SMTP with retry and logging ---
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
            server.login(sender_email, sender_password)
            server.send_message(msg, from_addr=sender_email, to_addrs=receivers)
            server.quit()
            break  # Success; exit the retry loop
        except Exception as e:
            if attempt < max_attempts - 1:
                time.sleep(5)  # Wait before retrying
            else:
                logger.error(f"Failed to send email after {max_attempts} attempts: {e}")
