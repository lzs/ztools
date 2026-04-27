#!/usr/bin/env bash

set -e

#export HTTPS_PROXY=socks5://127.0.0.1:5001

# Set this to your userid if it is different from your local computer.
USER=laizs

export VAULT_ADDR=https://bao.n15r.13cd.org
export VAULT_NAMESPACE=sshca

ROLE="user-cert"
KEY="$HOME/.ssh/id_ed25519"
CERT="$HOME/.ssh/id_ed25519-cert.pub"

# Locate the OpenBao binary
if command -v openbao.bao >/dev/null 2>&1; then
    BAO="openbao.bao"
elif command -v bao >/dev/null 2>&1; then
    BAO="bao"
else
    echo "Error: OpenBao binary not found." >&2
    exit 1
fi

# Check if token is valid
if $BAO token lookup >/dev/null 2>&1; then
    echo "Using existing OpenBao token"
else
    echo "No valid token, logging in..."
    $BAO login -method=ldap username="$USER" > /dev/null
fi

# Ensure key exists
if [ ! -f "$KEY" ]; then
    ssh-keygen -t ed25519 -f "$KEY"
fi

# Request certificate
$BAO write -field=signed_key ssh-client-signer/sign/$ROLE \
  public_key=@"$KEY.pub" \
  ttl=8h \
  > "$CERT"
