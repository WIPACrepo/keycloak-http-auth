#!/bin/bash

cat >/tmp/prom_cfg <<EOF
global:
  scrape_interval: 15s
  evaluation_interval: 15s
scrape_configs:
  - job_name: nginx
    scrape_interval: 1s
    honor_labels: true
    static_configs:
    - targets: ["localhost:9091"]
      labels: {"app": "nginx"}
EOF

podman pod create -p 9091:9091 -p 9090:9090 --userns=keep-id prom
podman run --rm -d --name prom-main --pod prom -v /tmp/prom_cfg:/etc/prometheus/prometheus.yml prom/prometheus
podman run --rm -d --name prom-push --pod prom prom/pushgateway

echo "Prometheus is ready"

( trap exit SIGINT ; read -r -d '' _ </dev/tty ) ## wait for Ctrl-C

podman pod kill prom
podman pod rm prom
rm -rf /tmp/prom_cfg
