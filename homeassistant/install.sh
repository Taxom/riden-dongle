#!/bin/sh
# Install files only. No HA restart, API call or connection to the PSU.
set -eu

source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target_dir=${1:-}
if [ -z "$target_dir" ]; then
    if [ -f /config/configuration.yaml ]; then
        target_dir=/config
    elif [ -f /homeassistant/configuration.yaml ]; then
        target_dir=/homeassistant
    else
        printf '%s\n' 'Specify the Home Assistant config directory: sh install.sh /config' >&2
        exit 1
    fi
fi
if [ ! -f "$target_dir/configuration.yaml" ]; then
    printf '%s\n' 'configuration.yaml was not found in the destination. Nothing installed.' >&2
    exit 1
fi
if [ ! -f "$source_dir/custom_components/rd6006p/manifest.json" ] || [ ! -f "$source_dir/www/rd6006p-card.js" ]; then
    printf '%s\n' 'Extract the complete archive before running install.sh.' >&2
    exit 1
fi

mkdir -p "$target_dir/custom_components" "$target_dir/www"
stage_dir="$target_dir/custom_components/.rd6006p-stage-$$"
trap 'if [ -d "$stage_dir" ]; then rm -rf -- "$stage_dir"; fi' EXIT HUP INT TERM
cp -R "$source_dir/custom_components/rd6006p" "$stage_dir"
if [ -e "$target_dir/custom_components/rd6006p" ] || [ -f "$target_dir/www/rd6006p-card.js" ]; then
    backup_dir="$target_dir/rd6006p_backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p "$backup_dir"
    if [ -e "$target_dir/custom_components/rd6006p" ]; then
        mv "$target_dir/custom_components/rd6006p" "$backup_dir/rd6006p"
    fi
    if [ -f "$target_dir/www/rd6006p-card.js" ]; then
        cp "$target_dir/www/rd6006p-card.js" "$backup_dir/rd6006p-card.js"
    fi
    printf 'Previous files: %s\n' "$backup_dir"
fi
mv "$stage_dir" "$target_dir/custom_components/rd6006p"
cp "$source_dir/www/rd6006p-card.js" "$target_dir/www/rd6006p-card.js"
printf '%s\n' 'Installed RD6006P 0.1.2. Next: check configuration and restart HA Core.'
printf '%s\n' 'Then add Riden RD6006P in Devices & services.'
printf '%s\n' 'Dashboard resource: /local/rd6006p-card.js?v=0.1.2 (JavaScript module)'
