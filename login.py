#!/usr/bin/env python3
"""
login.py — Login inicial no Garmin Connect (rodar UMA VEZ no Mac, nunca no CI).

Uso:
  python3 login.py

Salva tokens em .garmin_tokens_v3/garmin_tokens.json e instrui como
copiar para o Secret GARMIN_TOKENS do repositório no GitHub.
"""

import getpass
import sys
from pathlib import Path

TOKEN_DIR = Path(__file__).parent / ".garmin_tokens_v3"


def prompt_mfa() -> str:
    """Callback chamado pela lib quando a conta usa autenticação em 2 fatores."""
    return input("Código MFA (verificação em 2 etapas): ").strip()


def main():
    print("=" * 55)
    print("  Login Garmin Connect — geração de tokens OAuth")
    print("=" * 55)
    print()
    print("Atenção: credenciais NÃO são salvas — apenas os tokens.")
    print()

    email    = input("E-mail Garmin Connect: ").strip()
    password = getpass.getpass("Senha Garmin Connect: ")

    if not email or not password:
        print("ERRO: e-mail e senha são obrigatórios.")
        raise SystemExit(1)

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)

    try:
        from garminconnect import Garmin
    except ImportError:
        print("ERRO: garminconnect não instalado. Execute:")
        print("  pip install -r requirements.txt")
        raise SystemExit(1)

    print()
    print("Conectando ao Garmin Connect...")
    try:
        # token_store é argumento de login(), NÃO do construtor
        client = Garmin(email, password, prompt_mfa=prompt_mfa)
        client.login(tokenstore=str(TOKEN_DIR))
    except Exception as e:
        print(f"\nERRO ao fazer login: {e}")
        print("Verifique e-mail, senha e conexão com a internet.")
        raise SystemExit(1)

    token_file = TOKEN_DIR / "garmin_tokens.json"
    if not token_file.exists():
        print("ERRO: tokens não foram salvos pela lib. Verifique a versão do garminconnect.")
        raise SystemExit(1)

    print(f"\n✓ Login realizado! Tokens salvos em: {token_file}")

    try:
        nome = client.get_full_name()
        print(f"✓ Usuário autenticado: {nome}")
    except Exception:
        pass

    print()
    print("=" * 55)
    print("  PRÓXIMO PASSO: Atualizar Secret GARMIN_TOKENS")
    print("=" * 55)
    print()
    print("1. Acesse o repositório no GitHub:")
    print("   Settings → Secrets and variables → Actions")
    print()
    print("2. Crie (ou atualize) o Secret GARMIN_TOKENS")
    print("   com o conteúdo abaixo (copie TUDO, incluindo as chaves {}):")
    print()
    print("-" * 55)
    print(token_file.read_text(encoding="utf-8"))
    print("-" * 55)
    print()
    print("3. Se existirem os Secrets antigos GARMIN_OAUTH1 e")
    print("   GARMIN_OAUTH2, você pode removê-los.")
    print()
    print("4. Adicione o Secret GH_PAT_SECRETS:")
    print("   Fine-grained PAT com permissão 'Secrets: Read and write'")
    print("   neste repositório (necessário para auto-renovação dos tokens).")
    print()
    print("Feito! O workflow renovará os tokens automaticamente a cada run.")


if __name__ == "__main__":
    main()
