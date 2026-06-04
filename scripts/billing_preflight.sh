#!/usr/bin/env bash
# billing_preflight.sh — verify the billing environment before a deploy.
#
# Run this BEFORE ``make restart-api`` whenever you've touched billing
# config.  Exit 0 means the env is consistent; exit non-zero means
# the next API restart would either fail-fast or silently misbehave.
#
# What it checks:
#   - BILLING_PROVIDER is one of {stub, stripe}
#   - In stripe mode: STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET set and
#     well-formed (sk_… / whsec_…), and at least one tier price id set
#   - Stripe price ids look like Stripe price ids (price_…)
#   - SYSTEM_OWNER_IDS is set (otherwise no one can grant comps)
#   - APP_BASE_URL is HTTPS in production-looking config
#   - The dashboard build is fresh enough that the new Billing.tsx
#     fields will be present (file presence check)
#
# Reads .env from $PWD (the project root) by default; override with
# BILLING_PREFLIGHT_ENV=/path/to/.env.
#
# Designed to be safe to run in production — read-only.

set -uo pipefail

ENV_FILE="${BILLING_PREFLIGHT_ENV:-.env}"
RED='\033[31m'; GREEN='\033[32m'; YELLOW='\033[33m'; DIM='\033[2m'; RST='\033[0m'

fail_count=0
warn_count=0

_fail() { printf "${RED}✗${RST} %s\n" "$*"; fail_count=$((fail_count + 1)); }
_warn() { printf "${YELLOW}!${RST} %s\n" "$*"; warn_count=$((warn_count + 1)); }
_ok()   { printf "${GREEN}✓${RST} %s\n" "$*"; }
_dim()  { printf "${DIM}%s${RST}\n" "$*"; }

# ── Load env ───────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
    _fail "Env file not found: $ENV_FILE"
    echo "  Set BILLING_PREFLIGHT_ENV=/path/to/.env or run from project root."
    exit 2
fi

# Use ``set -a`` so sourced vars become env vars.  ``set -e`` is off so
# a malformed .env doesn't crash the script before we've reported it.
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# ── BILLING_PROVIDER ───────────────────────────────────────────
provider="${BILLING_PROVIDER:-stub}"
case "$provider" in
    stub)
        _ok "BILLING_PROVIDER=stub (no real charges)"
        _dim "  Switch to BILLING_PROVIDER=stripe when ready for production billing."
        ;;
    stripe)
        _ok "BILLING_PROVIDER=stripe"
        ;;
    *)
        _fail "BILLING_PROVIDER=$provider — must be 'stub' or 'stripe'"
        ;;
esac

# ── Stripe-mode checks ─────────────────────────────────────────
if [[ "$provider" == "stripe" ]]; then
    # Secret key
    if [[ -z "${STRIPE_SECRET_KEY:-}" ]]; then
        _fail "STRIPE_SECRET_KEY is empty — Stripe SDK won't authenticate."
    elif [[ "$STRIPE_SECRET_KEY" =~ ^sk_test_ ]]; then
        _warn "STRIPE_SECRET_KEY is a TEST key (sk_test_…) — real customers won't be charged."
    elif [[ "$STRIPE_SECRET_KEY" =~ ^sk_live_ ]]; then
        _ok "STRIPE_SECRET_KEY is a LIVE key"
    else
        _fail "STRIPE_SECRET_KEY doesn't look like a Stripe secret key (expected sk_live_… or sk_test_…)."
    fi

    # Webhook secret — Day 1 fail-fast requires this in stripe mode
    if [[ -z "${STRIPE_WEBHOOK_SECRET:-}" ]]; then
        _fail "STRIPE_WEBHOOK_SECRET is empty — handle_webhook will refuse to process events."
        echo "  Get the value from Stripe Dashboard → Developers → Webhooks → your endpoint → Signing secret."
    elif [[ ! "$STRIPE_WEBHOOK_SECRET" =~ ^whsec_ ]]; then
        _fail "STRIPE_WEBHOOK_SECRET doesn't look like a Stripe webhook secret (expected whsec_…)."
    else
        _ok "STRIPE_WEBHOOK_SECRET set"
    fi

    # Tier prices — at least one of starter / pro must be set or the
    # checkout endpoint will reject every plan choice
    have_tier=0
    for tier in STARTER PRO; do
        var="STRIPE_PRICE_$tier"
        val="${!var:-}"
        if [[ -n "$val" ]]; then
            if [[ "$val" =~ ^price_ ]]; then
                _ok "$var set ($val)"
                have_tier=$((have_tier + 1))
            else
                _fail "$var doesn't look like a Stripe price id (expected price_…)."
            fi
        else
            _warn "$var is empty — '$tier' tier checkout will be unavailable."
        fi
    done
    if [[ $have_tier -eq 0 ]]; then
        _fail "No tier price ids set — checkout will fail for every plan."
    fi

    # Extras price — required for the two-line subscription model
    # (Day 4).  Without it the API falls back to single-line checkout
    # and per-vehicle billing is silently disabled.
    if [[ -z "${STRIPE_PRICE_EXTRA_VEHICLE:-}" ]]; then
        _warn "STRIPE_PRICE_EXTRA_VEHICLE is empty — per-vehicle billing disabled."
        echo "  Customers will only be charged the flat tier price regardless of fleet size."
    elif [[ ! "${STRIPE_PRICE_EXTRA_VEHICLE}" =~ ^price_ ]]; then
        _fail "STRIPE_PRICE_EXTRA_VEHICLE doesn't look like a Stripe price id."
    else
        _ok "STRIPE_PRICE_EXTRA_VEHICLE set"
    fi
fi

# ── Comp account admin gating ──────────────────────────────────
if [[ -z "${SYSTEM_OWNER_IDS:-}" ]]; then
    _warn "SYSTEM_OWNER_IDS is empty — no one can grant complimentary accounts."
    echo "  Set to a comma-separated list of Telegram user_ids (e.g. ``SYSTEM_OWNER_IDS=12345,67890``)."
fi

# ── Public URL sanity (used in checkout success/cancel URLs) ───
base="${APP_BASE_URL:-https://4truck.us}"
if [[ "$base" =~ ^http:// ]]; then
    _warn "APP_BASE_URL is http:// — Stripe rejects non-HTTPS callback URLs in live mode."
fi

# ── Enforcement gating ─────────────────────────────────────────
if [[ "${BILLING_ENFORCEMENT_ENABLED:-0}" == "1" ]]; then
    _ok "BILLING_ENFORCEMENT_ENABLED=1 — non-paying accounts will get HTTP 402 past grace."
else
    _dim "BILLING_ENFORCEMENT_ENABLED is off — no account will be cut off (soft launch default)."
fi

# ── Dashboard build freshness ──────────────────────────────────
# Day 8 added new fields to the Billing page; an old build won't render
# banners / line items.  Look for one of the new strings in the dist
# bundle as a smoke check.
dist_dir="interfaces/dashboard/dist/assets"
if [[ -d "$dist_dir" ]]; then
    if grep -ql "Complimentary Account\|is_comped" "$dist_dir"/*.js 2>/dev/null; then
        _ok "Dashboard dist appears to contain Day 8 billing UI"
    else
        _warn "Dashboard dist may be stale — rebuild with ``make dashboard-build`` to ship the new Billing UI."
    fi
else
    _dim "interfaces/dashboard/dist not present — run ``make dashboard-build`` before deploy."
fi

# ── Summary ────────────────────────────────────────────────────
echo
if [[ $fail_count -gt 0 ]]; then
    printf "${RED}billing pre-flight: %d failure(s), %d warning(s)${RST}\n" "$fail_count" "$warn_count"
    exit 1
fi
if [[ $warn_count -gt 0 ]]; then
    printf "${YELLOW}billing pre-flight: 0 failures, %d warning(s) — proceed with care${RST}\n" "$warn_count"
    exit 0
fi
printf "${GREEN}billing pre-flight: all checks passed${RST}\n"
