#!/usr/bin/env python3
"""
Login único no Garmin Connect — salva o token para que sync.py não precise de senha.
"""
import getpass
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

TOKEN_DIR = str(Path(__file__).parent / ".garmin_tokens")

from garminconnect import Garmin

print("=" * 50)
print("  Login único no Garmin Connect")
print("=" * 50)
print()

email    = input("Seu e-mail do Garmin: ").strip()
password = getpass.getpass("Sua senha do Garmin (não aparece na tela): ")

print("\nConectando...")
client = Garmin(email=email, password=password)

try:
    client.login()
except Exception as e:
    msg = str(e)
    if any(w in msg.lower() for w in ["2fa", "mfa", "verification", "code", "one-time", "factor"]):
        print("\nGarmin pediu verificação em dois fatores (2FA).")
        code = input("Digite o código que chegou no seu e-mail/app: ").strip()
        # garth handles 2FA via resume_login or directly
        try:
            client.garth.resume_login(code)
        except Exception:
            client.login()
    else:
        print(f"\nErro ao conectar: {e}")
        raise

name = client.get_full_name()
print(f"\nConectado com sucesso como: {name}")

# Save token using garth
Path(TOKEN_DIR).mkdir(exist_ok=True)
client.garth.dump(TOKEN_DIR)

print(f"Token salvo em: {TOKEN_DIR}")
print("\nPronto! Agora pode rodar o sync.py normalmente.")
print("=" * 50)
