import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

from config import EmailConfig

def send_email_with_attachments(
    sender_email: str,
    sender_password: str,
    receiver_email: str,
    subject: str,
    body: str,
    attachments: list,
    smtp_server: str = "smtp.gmail.com",
    smtp_port: int = 587
):
    """
    Send an email with multiple file attachments.
    Supports Excel (.xlsx, .xls) and image files (.png, .jpg, etc.)

    Args:
        sender_email: your email address
        sender_password: your app-specific password or SMTP auth code
        receiver_email: recipient address
        subject: email subject line
        body: plain text email body
        attachments: list of file paths
        smtp_server: SMTP server hostname
        smtp_port: SMTP port (587 for TLS)
    """
    # --- Create email ---
    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
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
        server.send_message(msg)
        server.quit()
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

if __name__ == "__main__":
        # Add your generated files dynamically
    EmailConfig.ATTACHMENTS = [
        f"excel/2025-11-04.xlsx",
        "excel/trendings.xlsx",
        "excel/trendings_trend_break.png",
        "excel/trendings_trend_down.png",
        "excel/trendings_trend_even.png",
        "excel/trendings_trend_up.png"
    ]

    # Send the email by unpacking the config dict
    send_email_with_attachments(**EmailConfig.as_dict())