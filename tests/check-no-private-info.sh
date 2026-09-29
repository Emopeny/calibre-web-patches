#!/bin/bash
# 推送前的隐私闸门: 扫一遍**将要提交的文件**里有没有私人信息。
# 起因: 长期规则 —— 代码入仓库不得带私人域名/主机名/IP/账号/token。
#
# 用法: bash tests/check-no-private-info.sh [仓库根目录]
#
# 站点专属关键词（自己的域名、主机名、账号名…）**不要写进本文件**, 放两处之一:
#   1) 环境变量 BOOKWISH_PRIVACY_PATTERNS（正则片段, 多词用 | 分隔）
#   2) 仓库根目录下的 .privacy-patterns（每行一个词, 已被 .gitignore 忽略）
# 本文件里只保留与站点无关的通用模式 —— 免得闸门自己把关键词带进仓库。
# 退出码非 0 表示命中, 不要推。
set -u
ROOT=${1:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$ROOT" || exit 1

GENERIC='ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|\b10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\b|\b192\.168\.[0-9]{1,3}\.[0-9]{1,3}\b|\b172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}\b|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'

OWN=${BOOKWISH_PRIVACY_PATTERNS:-}
if [ -z "$OWN" ] && [ -f .privacy-patterns ]; then
  # 逐行读, 跳过空行与 # 注释, 拼成正则
  OWN=$(grep -vE '^[[:space:]]*(#|$)' .privacy-patterns | paste -sd'|' -)
fi
PATTERNS="$GENERIC${OWN:+|$OWN}"

files=$(git ls-files 2>/dev/null | grep -v 'check-no-private-info.sh')
if [ -z "$files" ]; then
  files=$(find . -type f \( -name '*.py' -o -name '*.sh' -o -name '*.md' -o -name '.env.example' -o -name '*.json' \) \
          -not -path './.git/*' -not -path './__pycache__/*' -not -name 'check-no-private-info.sh')
fi

# -i 是必须的: 大小写敏感的版本会漏掉驼峰写法的名字
hits=$(echo "$files" | xargs -r grep -IniE "$PATTERNS" 2>/dev/null)
if [ -n "$hits" ]; then
  echo "❌ 命中私人信息, 不要推送:"
  echo "$hits" | sed 's/^/    /'
  exit 1
fi
if [ -n "$OWN" ]; then extra="已载入站点关键词"; else extra="未配置站点关键词（只查通用模式）"; fi
echo "✅ 隐私闸门通过（$(echo "$files" | wc -l) 个文件; $extra）"
