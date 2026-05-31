#!/bin/bash
# Generuje self-signed certyfikat TLS dla serwera RCMP
# Użycie: bash scripts/generate_certs.sh

set -e

CERTS_DIR="certs"
KEY_FILE="$CERTS_DIR/server.key"
CERT_FILE="$CERTS_DIR/server.crt"
DAYS=365
SUBJECT="/C=PL/ST=Malopolska/L=Krakow/O=RCMP Chat/CN=localhost"

mkdir -p "$CERTS_DIR"

echo "[TLS] Generowanie klucza prywatnego..."
openssl genrsa -out "$KEY_FILE" 2048

echo "[TLS] Generowanie certyfikatu self-signed..."
openssl req -new -x509 \
    -key "$KEY_FILE" \
    -out "$CERT_FILE" \
    -days "$DAYS" \
    -subj "$SUBJECT" \
    -addext "subjectAltName=IP:127.0.0.1,DNS:localhost"

echo "[TLS] Gotowe:"
echo "  Klucz:       $KEY_FILE"
echo "  Certyfikat:  $CERT_FILE"
echo ""
openssl x509 -in "$CERT_FILE" -noout -text | grep -E "Subject:|Not (Before|After)|DNS:|IP:"