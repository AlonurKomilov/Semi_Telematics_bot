#!/usr/bin/env bash
# abc-skills validate — run before every commit and before any claude.ai upload
fail=0
yamlwarn=""
for f in */SKILL.md; do
  dir=$(dirname "$f")
  fm=$(awk '/^---$/{n++; next} n==1{print} n==2{exit}' "$f")
  name=$(printf '%s\n' "$fm" | awk -F': ' '/^name:/{print $2; exit}')
  ver=$(printf '%s\n' "$fm" | awk -F': ' '/^version:/{print $2; exit}')
  family=$(printf '%s\n' "$fm" | awk -F': ' '/^family:/{print $2; exit}')
  domain=$(printf '%s\n' "$fm" | awk -F': ' '/^domain:/{print $2; exit}')
  kind=$(printf '%s\n' "$fm" | awk -F': ' '/^kind:/{print $2; exit}')
  method=$(printf '%s\n' "$fm" | awk -F': ' '/^method:/{print $2; exit}')
  scope=$(printf '%s\n' "$fm" | awk -F': ' '/^scope:/{print $2; exit}')
  desc=$(printf '%s\n' "$fm" | awk '/^description:/{sub(/^description: /,""); print; exit}')
  dlen=$(printf %s "$desc" | wc -c)
  ok=1
  if command -v python3 >/dev/null 2>&1 && python3 -c "import yaml" 2>/dev/null; then
    printf '%s\n' "$fm" | python3 -c "import sys,yaml; yaml.safe_load(sys.stdin.read())" 2>/dev/null \
      || { echo "FAIL $f: frontmatter is not strict YAML (GitHub jadval chizmaydi)"; ok=0; }
  else
    yamlwarn=1
  fi
  [ -z "$name" ] && { echo "FAIL $f: frontmatter name yo'q"; ok=0; }
  echo "$name" | grep -Eq '^[a-z0-9][a-z0-9-]{0,63}$' || { echo "FAIL $f: name format ('$name')"; ok=0; }
  [ "$dlen" -gt 1024 ] && { echo "FAIL $f: description ${dlen}B > 1024"; ok=0; }
  [ "$dir" != "$name" ] && echo "WARN $f: papka '$dir' != name '$name'"
  echo "$ver" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || echo "WARN $f: version yo'q yoki semver emas ('$ver')"
  [ -z "$family" ] && { echo "FAIL $f: family yo'q"; ok=0; }
  [ -z "$domain" ] && { echo "FAIL $f: domain yo'q"; ok=0; }
  [ -z "$kind" ] && { echo "FAIL $f: kind yo'q"; ok=0; }
  [ -z "$method" ] && [ -z "$scope" ] && { echo "FAIL $f: method va scope ikkalasi ham yo'q — kamida bittasi kerak"; ok=0; }
  if [ -n "$domain" ] && [ -n "$kind" ]; then
    expected="$domain-$kind"
    [ -n "$method" ] && expected="$expected-$method"
    [ -n "$scope" ] && expected="$expected-$scope"
    [ "$name" != "$expected" ] && { echo "FAIL $f: name-invariant — name='$name' expected='$expected'"; ok=0; }
  fi
  [ $ok -eq 1 ] && echo "OK   $name v${ver:-?}  family=$family domain=$domain kind=$kind method=${method:--} scope=${scope:--} (desc ${dlen}B)" || fail=1
done
[ -n "$yamlwarn" ] && echo "WARN: python3+pyyaml topilmadi — strict-YAML tekshiruvi o'tkazib yuborildi"
exit $fail
