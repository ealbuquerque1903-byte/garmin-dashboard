#!/usr/bin/env python3
"""
Envia PDFs de reports/ por e-mail via SMTP (stdlib apenas).

Variáveis de ambiente obrigatórias:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO

Comportamento:
  - Se reports/ tiver PDFs, envia como anexos (divide em múltiplos e-mails se > 20 MB).
  - Se reports/ estiver vazio, envia e-mail informando "Nenhuma atividade nova".
  - Porta 465 → SMTP_SSL; caso contrário → starttls().
"""

import os
import smtplib
import sys
from datetime import date
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE       = Path(__file__).parent
REPORTS    = BASE / "reports"
MAX_BYTES  = 20 * 1024 * 1024  # 20 MB por e-mail

def get_env(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if not v:
        print(f"ERRO: variável de ambiente {key} não definida ou vazia.")
        raise SystemExit(1)
    return v

def build_smtp():
    host = get_env("SMTP_HOST")
    port = int(get_env("SMTP_PORT"))
    user = get_env("SMTP_USER")
    pwd  = get_env("SMTP_PASS")

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        server.starttls()
        server.ehlo()

    server.login(user, pwd)
    return server

def send_message(server, msg):
    mail_from = get_env("MAIL_FROM")
    mail_to   = get_env("MAIL_TO")
    msg["From"] = mail_from
    msg["To"]   = mail_to
    server.sendmail(mail_from, [mail_to], msg.as_string())

def attach_pdf(msg: MIMEMultipart, pdf_path: Path):
    with open(pdf_path, "rb") as f:
        part = MIMEBase("application", "pdf")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=pdf_path.name)
    msg.attach(part)

def build_body(pdfs: list[Path]) -> str:
    if not pdfs:
        return "Nenhum PDF gerado nesta execução."
    lines = ["PDFs em anexo:", ""]
    for p in pdfs:
        lines.append(f"  • {p.name}")
    return "\n".join(lines)

def main():
    hoje = date.today().isoformat()

    pdfs = sorted(REPORTS.glob("*.pdf")) if REPORTS.exists() else []

    server = build_smtp()
    try:
        if not pdfs:
            # ── E-mail sem anexo: informa run OK sem atividade nova ──────────
            msg = MIMEMultipart()
            msg["Subject"] = f"Garmin: nenhuma atividade nova — {hoje}"
            msg.attach(MIMEText(
                "O sync foi concluído com sucesso.\n"
                "Nenhuma atividade nova encontrada desde o último run.\n",
                "plain", "utf-8"
            ))
            send_message(server, msg)
            print(f"✓ E-mail de status enviado (sem atividades novas).")
        else:
            # ── Dividir em lotes de ≤ 20 MB ──────────────────────────────────
            batches: list[list[Path]] = []
            current_batch: list[Path] = []
            current_size = 0

            for pdf in pdfs:
                sz = pdf.stat().st_size
                if current_batch and current_size + sz > MAX_BYTES:
                    batches.append(current_batch)
                    current_batch = [pdf]
                    current_size  = sz
                else:
                    current_batch.append(pdf)
                    current_size += sz

            if current_batch:
                batches.append(current_batch)

            total = len(pdfs)
            for i, batch in enumerate(batches, 1):
                suffix = f" ({i}/{len(batches)})" if len(batches) > 1 else ""
                msg = MIMEMultipart()
                msg["Subject"] = f"Garmin: {total} relatório(s) — {hoje}{suffix}"
                msg.attach(MIMEText(build_body(batch), "plain", "utf-8"))
                for pdf in batch:
                    attach_pdf(msg, pdf)
                send_message(server, msg)
                print(f"✓ E-mail {i}/{len(batches)} enviado ({len(batch)} PDF(s)).")
    finally:
        server.quit()

if __name__ == "__main__":
    main()
