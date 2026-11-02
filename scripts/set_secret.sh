#!/usr/bin/env bash
# .env의 값 하나를 화면에 보이지 않게 입력해 저장합니다.
# 사용: bash scripts/set_secret.sh GEMINI_API_KEY
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
NAME="${1:-}"

if ! [[ "$NAME" =~ ^[A-Z][A-Z0-9_]*$ ]]; then
  echo "사용법: bash scripts/set_secret.sh 이름 (예: GEMINI_API_KEY)" >&2
  exit 1
fi
if ! grep -q "^${NAME}=" "$ROOT/.env.example"; then
  echo "${NAME}은(는) .env.example에 없는 이름입니다." >&2
  exit 1
fi

[ -f "$ENV_FILE" ] || { umask 077; cp "$ROOT/.env.example" "$ENV_FILE"; }
chmod 600 "$ENV_FILE"

read -r -s -p "${NAME} 값을 붙여 넣고 Enter (화면에 안 보입니다): " VALUE
echo
VALUE="$(printf '%s' "$VALUE" | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [ -z "$VALUE" ]; then
  echo "값이 비어 있어 저장하지 않았습니다." >&2
  exit 1
fi

TMP="$(mktemp "$ROOT/.env.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
chmod 600 "$TMP"
if grep -q "^${NAME}=" "$ENV_FILE"; then
  NAME="$NAME" VALUE="$VALUE" awk -F= 'BEGIN{n=ENVIRON["NAME"]; v=ENVIRON["VALUE"]}
    $1==n {print n "=" v; next} {print}' "$ENV_FILE" > "$TMP"
else
  cat "$ENV_FILE" > "$TMP"
  printf '%s=%s\n' "$NAME" "$VALUE" >> "$TMP"
fi
mv "$TMP" "$ENV_FILE"
trap - EXIT
echo "${NAME} 저장 완료 (${#VALUE}자)"
