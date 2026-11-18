#!/bin/sh
# Native builds: run `image` on each supported architecture, then `bundle`.
set -eu
base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$base"
mkdir -p dist/images
fingerprint=$(sha256sum Dockerfile server.py API_USAGE.txt | sha256sum | cut -c1-12)
tag="webnote:portable-$fingerprint"
case ${1:-} in
    image)
        case $(uname -m) in
            x86_64|amd64) arch=amd64 ;;
            aarch64|arm64) arch=arm64 ;;
            *) echo 'Unsupported native architecture.' >&2; exit 1 ;;
        esac
        docker build --tag "$tag" .
        actual=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$tag")
        [ "$actual" = "linux/$arch" ] || { echo "Unexpected image platform: $actual" >&2; exit 1; }
        tmp=$(mktemp "$base/dist/image.XXXXXX")
        trap 'rm -f "$tmp" "$tmp.gz"' EXIT HUP INT TERM
        docker image save --output "$tmp" "$tag"
        gzip -c "$tmp" > "$tmp.gz"
        mv "$tmp.gz" "dist/images/webnote-linux-$arch.tar.gz"
        printf '%s\n' "$tag" > "dist/images/$arch.tag"
        echo "Saved linux/$arch image: $tag"
        ;;
    bundle)
        for arch in amd64 arm64; do
            [ -s "dist/images/webnote-linux-$arch.tar.gz" ]
            [ "$(cat "dist/images/$arch.tag")" = "$tag" ] || { echo "Rebuild $arch image from matching source." >&2; exit 1; }
        done
        tmp=$(mktemp -d "$base/dist/bundle.XXXXXX")
        trap 'rm -rf "$tmp"' EXIT HUP INT TERM
        release="$tmp/webnote-portable"
        mkdir -p "$release/images"
        for file in LICENSE Dockerfile .dockerignore server.py API_USAGE.txt README.md AGENTS.md PORTABLE.md PORTABILITY_VERIFICATION.md compose.portable.yaml test_server.py; do cp "$file" "$release/$file"; done
        cp webnote.sh build-release.sh "$release/"
        chmod 0755 "$release/webnote.sh" "$release/build-release.sh"
        cp dist/images/webnote-linux-*.tar.gz "$release/images/"
        printf '%s\n' "$tag" > "$release/image-tag.txt"
        (cd "$release" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
        tar -C "$tmp" -czf "$tmp/webnote-portable.tar.gz" webnote-portable
        mv "$tmp/webnote-portable.tar.gz" dist/webnote-portable.tar.gz
        (cd dist && sha256sum webnote-portable.tar.gz > webnote-portable.tar.gz.sha256)
        echo "Created dist/webnote-portable.tar.gz ($tag)"
        ;;
    *) echo 'Usage: ./build-release.sh image | bundle' >&2; exit 2 ;;
esac
