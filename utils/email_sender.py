"""Gmail SMTP 이메일 발송 (App Password 방식)"""
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_email(
    recipients: list,
    subject: str,
    body: str,
    attachments: list = None,
    html_body: str = None,
):
    """
    Gmail App Password로 이메일 발송.

    환경변수:
      GMAIL_USER          - 발신 Gmail 주소
      GMAIL_APP_PASSWORD  - Gmail 앱 비밀번호 (16자리)

    html_body 전달 시 HTML + 텍스트 멀티파트로 발송.
    """
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pw = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_user or not gmail_pw:
        raise RuntimeError("GMAIL_USER 또는 GMAIL_APP_PASSWORD 환경변수가 설정되지 않았습니다.")

    # 첨부파일이 있으면 mixed, 없으면 alternative(또는 plain)
    msg = MIMEMultipart("mixed")
    msg["From"] = gmail_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if html_body:
        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(body, "plain", "utf-8"))
        alternative.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(alternative)
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    if attachments:
        for path in attachments:
            if not path or not Path(path).is_file():
                logger.warning(f"첨부파일 없음: {path}")
                continue
            filename = Path(path).name
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), Name=filename)
            part["Content-Disposition"] = f'attachment; filename="{filename}"'
            msg.attach(part)
            logger.info(f"  첨부 파일 추가: {filename}")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(gmail_user, gmail_pw)
        smtp.sendmail(gmail_user, recipients, msg.as_bytes())

    logger.info(f"이메일 발송 완료 → {', '.join(recipients)}")
