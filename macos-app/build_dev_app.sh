#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_NAME="MiMo Workflow Dev"
BUNDLE_DIR="$SCRIPT_DIR/build/${APP_NAME}.app"
CONTENTS_DIR="$BUNDLE_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"
EXECUTABLE_NAME="MiMoWorkflowDev"
SOURCE_FILE="$SCRIPT_DIR/Sources/MiMoWorkflowDevApp.swift"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
NPM_BIN="${NPM_BIN:-$(command -v npm)}"
BACKEND_PORT="${BACKEND_PORT:-8765}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "python3 not found. Set PYTHON_BIN=/absolute/path/to/python3" >&2
  exit 1
fi

if [[ ! -x "$NPM_BIN" ]]; then
  echo "npm not found. Set NPM_BIN=/absolute/path/to/npm" >&2
  exit 1
fi

echo "Building frontend..."
(cd "$REPO_ROOT/apps/desktop" && "$NPM_BIN" run build)

echo "Preparing app bundle..."
rm -rf "$BUNDLE_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR" "$SCRIPT_DIR/build/logs"

echo "Compiling Swift app..."
swiftc \
  -framework Cocoa \
  -framework WebKit \
  "$SOURCE_FILE" \
  -o "$MACOS_DIR/$EXECUTABLE_NAME"

chmod +x "$MACOS_DIR/$EXECUTABLE_NAME"

PLIST="$CONTENTS_DIR/Info.plist"
plutil -create xml1 "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string $APP_NAME" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $APP_NAME" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleExecutable string $EXECUTABLE_NAME" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.mimo.workflow.dev" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundlePackageType string APPL" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string 0.1.0" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string 1" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 13.0" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity dict" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NSAppTransportSecurity:NSAllowsArbitraryLoads bool true" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :RepositoryRoot string $REPO_ROOT" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :PythonExecutable string $PYTHON_BIN" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :NpmExecutable string $NPM_BIN" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :BackendPort string $BACKEND_PORT" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :FrontendPort string $FRONTEND_PORT" "$PLIST"

if command -v codesign >/dev/null 2>&1; then
  echo "Ad-hoc signing app..."
  codesign --force --deep --sign - "$BUNDLE_DIR" >/dev/null
fi

echo "Built: $BUNDLE_DIR"
echo "Open with: open -n \"$BUNDLE_DIR\""
