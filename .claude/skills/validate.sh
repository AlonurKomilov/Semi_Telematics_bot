#!/usr/bin/env bash
# abc-skills validate — run before every commit and before any claude.ai upload
fail=0
for f in */SKILL.md; do
  name=$(awk -F': ' '/^name:/{print $2; exit}' "$f")
  desc=$(awk '/^description:/{sub(/^description: /,""); print; exit}' "$f")
  dlen=$(printf %s "$desc" | wc -c)
  dir=$(dirname "$f")
  ok=1
  [ -z "$name" ] && { echo "FAIL $f: frontmatter name yo'q"; ok=0; }
  echo "$name" | grep -Eq '^[a-z0-9][a-z0-9-]{0,63}$' || { echo "FAIL $f: name format ('$name')"; ok=0; }
  [ "$dlen" -gt 1024 ] && { echo "FAIL $f: description ${dlen}B > 1024"; ok=0; }
  [ "$dir" != "$name" ] && echo "WARN $f: papka '$dir' != name '$name'"
  [ $ok -eq 1 ] && echo "OK   $name (desc ${dlen}B)" || fail=1
done
exit $fail
