#!/usr/bin/env python3
"""
Configura a chave da API Anthropic para análises de IA.
Obtenha sua chave em: https://console.anthropic.com
"""
import getpass
from pathlib import Path

ENV_FILE = Path(__file__).parent / ".env"

print("=" * 50)
print("  Configuração da IA (Claude/Anthropic)")
print("=" * 50)
print()
print("Acesse https://console.anthropic.com para criar")
print("sua chave de API gratuita.")
print()

key = getpass.getpass("Cole sua ANTHROPIC_API_KEY (não aparece na tela): ").strip()

if not key.startswith("sk-ant-"):
    print("Aviso: a chave parece incorreta (deve começar com sk-ant-)")
    confirm = input("Continuar mesmo assim? (s/n): ").strip().lower()
    if confirm != "s":
        print("Abortado.")
        raise SystemExit(0)

# Load existing .env if any
lines = []
if ENV_FILE.exists():
    lines = [l for l in ENV_FILE.read_text().splitlines() if not l.startswith("ANTHROPIC_API_KEY=")]

lines.append(f"ANTHROPIC_API_KEY={key}")
ENV_FILE.write_text("\n".join(lines) + "\n")
ENV_FILE.chmod(0o600)

print()
print(f"Chave salva em: {ENV_FILE}")
print("Análises de IA estão agora ativadas!")
print("=" * 50)
