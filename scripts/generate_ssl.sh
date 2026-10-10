#!/usr/bin/env bash

################################################################################
# SSL Certificate Generation Script
# Creates self-signed SSL certificates with Subject Alternative Names (SAN)
# for local HTTPS development and production deployment
################################################################################

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/utils.sh"

log_header "SSL Certificate Generation"

# Get project root directory
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Change to project root
cd "$PROJECT_ROOT"

CERT_DIR="ssl"
KEY_FILE="$CERT_DIR/server.key"
CERT_FILE="$CERT_DIR/server.crt"
PEM_FILE="$CERT_DIR/server.pem"
OPENSSL_CONF="$CERT_DIR/openssl.cnf"

# Create SSL directory
mkdir -p "$CERT_DIR"

FORCE="${1:-}"

# Check if certificates already exist (unless --force is passed)
if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ] && [ "$FORCE" != "--force" ]; then
    log_warning "SSL certificates already exist (use --force to regenerate)"
    log_info "Existing certificates:"
    log_info "  Key:  ${KEY_FILE}"
    log_info "  Cert: ${CERT_FILE}"
    log_info "  PEM:  ${PEM_FILE}"
    exit 0
fi

# Detect local IP addresses for SANs
LOCAL_IPS=()
if command -v hostname >/dev/null 2>&1; then
    for ip in $(hostname -I 2>/dev/null); do
        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            LOCAL_IPS+=("$ip")
        fi
    done
fi

CUSTOM_HOST="${SSL_HOSTNAME:-${1:-}}"

log_info "Building OpenSSL configuration with Subject Alternative Names (SANs)..."

cat > "$OPENSSL_CONF" << EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
req_extensions = req_ext
x509_extensions = v3_ca

[dn]
C = IN
ST = Tamil Nadu
L = Coimbatore
O = Sri Shakthi Institute of Engineering & Technology
OU = Faculty Attendance System
CN = localhost

[req_ext]
subjectAltName = @alt_names

[v3_ca]
subjectAltName = @alt_names
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth, clientAuth

[alt_names]
DNS.1 = localhost
DNS.2 = *.localhost
DNS.3 = host.docker.internal
DNS.4 = app
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

# Append detected IPs and custom hosts to alt_names
ALT_INDEX=3
IP_INDEX=3
for ip in "${LOCAL_IPS[@]}"; do
    if [ "$ip" != "127.0.0.1" ]; then
        echo "IP.$IP_INDEX = $ip" >> "$OPENSSL_CONF"
        IP_INDEX=$((IP_INDEX + 1))
    fi
done

if [ -n "$CUSTOM_HOST" ] && [ "$CUSTOM_HOST" != "--force" ]; then
    if [[ "$CUSTOM_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "IP.$IP_INDEX = $CUSTOM_HOST" >> "$OPENSSL_CONF"
    else
        echo "DNS.5 = $CUSTOM_HOST" >> "$OPENSSL_CONF"
    fi
fi

log_info "Generating private key (2048-bit RSA)..."
openssl genrsa -out "$KEY_FILE" 2048 > /dev/null 2>&1
chmod 600 "$KEY_FILE"
log_success "Private key generated (${KEY_FILE})"

log_info "Generating self-signed certificate with SANs (365-day validity)..."
openssl req -x509 -nodes -days 365 \
    -key "$KEY_FILE" \
    -out "$CERT_FILE" \
    -config "$OPENSSL_CONF" \
    -extensions v3_ca \
    > /dev/null 2>&1
chmod 644 "$CERT_FILE"
log_success "Self-signed certificate generated with SANs (${CERT_FILE})"

log_info "Creating PEM bundle (combined key and certificate)..."
cat "$KEY_FILE" "$CERT_FILE" > "$PEM_FILE"
chmod 600 "$PEM_FILE"
log_success "PEM bundle created (${PEM_FILE})"

# Clean up temporary openssl.cnf
rm -f "$OPENSSL_CONF"

# Display summary
log_header "SSL Certificate Generation Complete"
log_success "SSL certificates generated successfully"
echo ""
log_info "Certificate Details:"
echo -e "  ${CYAN}Key File:${RESET}  ${KEY_FILE}"
echo -e "  ${CYAN}Cert File:${RESET} ${CERT_FILE}"
echo -e "  ${CYAN}PEM File:${RESET}  ${PEM_FILE}"
echo ""
log_info "SANs Included:"
openssl x509 -in "$CERT_FILE" -noout -text 2>/dev/null | grep -A 1 "Subject Alternative Name" | tail -n 1 | sed 's/^[ \t]*/  /' || true
echo ""
log_info "Certificates are ready for HTTPS connections on port \${APP_PORT:-5860}"

