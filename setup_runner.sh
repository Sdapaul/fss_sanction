#!/bin/bash
# Oracle Cloud Ubuntu VM에서 GitHub Actions self-hosted runner 설치
# 사용법: bash setup_runner.sh <RUNNER_TOKEN>

set -e

RUNNER_TOKEN="${1:?사용법: bash setup_runner.sh <RUNNER_TOKEN>}"
REPO_URL="https://github.com/Sdapaul/fss_sanction"
RUNNER_VERSION="2.335.1"
RUNNER_NAME="oracle-cloud-seoul"

echo "=== 1. 시스템 패키지 업데이트 ==="
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git curl wget

echo "=== 2. Python 의존성 설치 ==="
pip3 install --break-system-packages requests beautifulsoup4 lxml openpyxl pytz python-dotenv 2>/dev/null \
  || pip3 install requests beautifulsoup4 lxml openpyxl pytz python-dotenv

echo "=== 3. Runner 다운로드 ==="
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -fsSL -o runner.tar.gz \
  "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-arm64-${RUNNER_VERSION}.tar.gz"
tar xzf runner.tar.gz
rm runner.tar.gz

echo "=== 4. Runner 등록 ==="
./config.sh \
  --url "$REPO_URL" \
  --token "$RUNNER_TOKEN" \
  --name "$RUNNER_NAME" \
  --labels "self-hosted,Linux,ARM64,seoul" \
  --work "_work" \
  --unattended

echo "=== 5. 서비스 등록 (자동 시작) ==="
sudo ./svc.sh install
sudo ./svc.sh start

echo ""
echo "✓ 설치 완료! Runner 상태:"
sudo ./svc.sh status
