"""Sign the CI release APK and check its signer and embedded version before upload."""

import argparse
import os
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("unsigned_apk", type=Path)
    parser.add_argument("output_directory", type=Path)
    args = parser.parse_args()

    if not os.environ.get("AC8_ANDROID_SIGNING_PASSWORD"):
        parser.error("AC8_ANDROID_SIGNING_PASSWORD is required")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-(?:alpha|beta|rc)\.[0-9]+)?", args.tag):
        parser.error("Expected an Android release tag such as v1.2.3")
    tools = Path(os.environ["ANDROID_HOME"]) / "build-tools" / "36.0.0"
    java = Path(os.environ["JAVA_HOME"]) / "bin" / ("java.exe" if os.name == "nt" else "java")
    apksigner = [java, "-jar", tools / "lib" / "apksigner.jar"]
    aapt = tools / ("aapt.exe" if os.name == "nt" else "aapt")
    signing = Path(__file__).resolve().parents[1] / "android" / "signing"

    manifest = subprocess.check_output([aapt, "dump", "badging", args.unsigned_apk], encoding="utf-8")
    package = next(line for line in manifest.splitlines() if line.startswith("package: "))
    attributes = dict(re.findall(r"(\w+)='([^']*)'", package))
    if (attributes["name"] != "local.ac8.mobile" or attributes["versionName"] != args.tag[1:]
            or int(attributes["versionCode"]) <= 0 or "application-debuggable" in manifest.splitlines()):
        raise SystemExit(f"Unexpected release APK metadata: {package}")

    args.output_directory.mkdir(parents=True, exist_ok=True)
    apk = args.output_directory / f"AC8-Music-Player-{args.tag}-Android.apk"
    subprocess.run([*apksigner, "sign", "--ks", signing / "release.p12", "--ks-key-alias", "ac8-player",
                    "--ks-pass", "env:AC8_ANDROID_SIGNING_PASSWORD", "--v4-signing-enabled", "false",
                    "--out", apk, args.unsigned_apk], check=True)
    report = subprocess.check_output([*apksigner, "verify", "--verbose", "--print-certs", apk], encoding="utf-8")
    digests = re.findall(r"^Signer #\d+ certificate SHA-256 digest: (\w+)$", report, re.MULTILINE)
    expected = (signing / "certificate.sha256").read_text(encoding="ascii").strip()
    if digests != [expected]:
        raise SystemExit("Release APK signer does not match android/signing/certificate.sha256")
    print(f"{apk.name}  versionCode={attributes['versionCode']}  signer SHA-256={expected}")


if __name__ == "__main__":
    main()
