#!/bin/sh

set -eu

cd /app

packages="$(awk -F'[=<>!~ ]' '/^[[:space:]]*[^#[:space:]]/{print $1}' requirements.txt | paste -sd' ' -)"
tmp_file="$(mktemp)"

cat > "$tmp_file" <<'EOF'
## Third-Party Dependency Licenses

This file is generated from the direct Python dependencies listed in
`requirements.txt` with:

```bash
make licenses
```

EOF

# shellcheck disable=SC2086
pip-licenses --from=mixed --format=markdown --with-urls --packages $packages >> "$tmp_file"

mv "$tmp_file" LICENSES.md
