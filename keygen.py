#!/usr/bin/env python3
"""
VPG Soluções em Tecnologia
Gerador de Código de Ativação — USO EXCLUSIVO DO FORNECEDOR

Como usar:
  python keygen.py
  → Informe o Machine ID enviado pelo cliente e o código será gerado.

  python keygen.py XXXXXXXX-YYYYYYYY-ZZZZZZZZ

NUNCA distribua este arquivo ao cliente.
"""

import sys
import hmac
import hashlib

# Deve ser idêntico ao _SECRET em main.py
_SECRET = b"p8MeLk78TCrOFczSkWRD3"


def generate_code(machine_id: str) -> str:
    clean = machine_id.strip().upper()
    sig = hmac.new(_SECRET, clean.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    return f"{sig[:4]}-{sig[4:8]}-{sig[8:12]}-{sig[12:16]}"


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        machine_id = sys.argv[1].strip()
    else:
        machine_id = input("Digite o Machine ID do cliente: ").strip()

    if not machine_id:
        print("Erro: Machine ID não informado.")
        sys.exit(1)

    code = generate_code(machine_id)
    print()
    print(f"  Machine ID    : {machine_id.upper()}")
    print(f"  Código gerado : {code}")
    print()
