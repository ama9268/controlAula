#!/bin/bash
# examen-fw: agente de Control de Examen. Se ejecuta como root en el PC del alumno.
#
# Bloquea la red salvo los destinos permitidos mediante una tabla nftables propia
# (inet examen), independiente de ufw. El bloqueo persiste tras reiniciar
# (examen-fw.service) y se retira solo al llegar la hora límite
# (examen-fw-expira.timer).
#
# Salida: líneas CLAVE=VALOR (ESTADO, HASTA, ERROR) que interpreta el PC del profesor.
# Compatible con bash 3.2 para poder probar generar-nft fuera de Ubuntu.
set -euo pipefail
umask 022
export LC_ALL=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

readonly BIN=/usr/local/sbin/examen-fw
readonly DIR=/etc/examen-fw
readonly REGLAS="$DIR/examen.nft"
readonly LIMITE="$DIR/limite"
readonly SYSTEMD=/etc/systemd/system
readonly SERVICIO=examen-fw.service
readonly TIMER=examen-fw-expira.timer
readonly SERVICIO_EXPIRA=examen-fw-expira.service

# Sin ceros a la izquierda: bash interpretaría 022 como octal.
readonly OCTETO='(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
readonly IP="$OCTETO\\.$OCTETO\\.$OCTETO\\.$OCTETO"
readonly RE_LISTA_IPS="^$IP(,$IP)*$"
readonly RE_LISTA_PUERTOS='^[1-9][0-9]{0,4}(,[1-9][0-9]{0,4})*$'
readonly RE_MINUTOS='^[1-9][0-9]{0,3}$'
readonly MAX_MINUTOS=1440

fallo() {
    echo "ERROR=$*"
    exit 1
}

uso() {
    cat <<'EOF'
Uso: examen-fw <acción> [opciones]
  bloquear --permitidas IP[,IP...] --puertos P[,P...] --minutos N
  desbloquear
  estado
  generar-nft --permitidas IP[,IP...] --puertos P[,P...]   (solo muestra las reglas)
Uso interno (systemd): aplicar, quitar-tabla
EOF
}

requiere_root() {
    [[ $EUID -eq 0 ]] || fallo "se necesita root"
    command -v nft >/dev/null || fallo "nftables (nft) no está instalado"
}

# Rellena PERMITIDAS, PUERTOS y MINUTOS a partir de las opciones, validándolas.
leer_opciones() {
    PERMITIDAS="" PUERTOS="" MINUTOS=""
    while (($#)); do
        (($# >= 2)) || fallo "falta el valor de $1"
        case $1 in
            --permitidas) PERMITIDAS=$2 ;;
            --puertos) PUERTOS=$2 ;;
            --minutos) MINUTOS=$2 ;;
            *) fallo "opción desconocida: $1" ;;
        esac
        shift 2
    done

    [[ $PERMITIDAS =~ $RE_LISTA_IPS ]] || fallo "lista de IPs permitidas no válida"
    [[ $PUERTOS =~ $RE_LISTA_PUERTOS ]] || fallo "lista de puertos no válida"
    local puerto ssh=no
    local -a puertos
    IFS=, read -r -a puertos <<<"$PUERTOS"
    for puerto in "${puertos[@]}"; do
        ((puerto <= 65535)) || fallo "puerto fuera de rango: $puerto"
        [[ $puerto != 22 ]] || ssh=si
    done
    # Sin SSH el profesor perdería el control del equipo.
    [[ $ssh == si ]] || fallo "el puerto 22 (SSH) debe estar permitido"
}

generar_nft() {
    cat <<EOF
# Generado por examen-fw. No editar: se regenera en cada bloqueo.
table inet examen
delete table inet examen
table inet examen {
	chain entrada {
		type filter hook input priority 0; policy drop;
		iif "lo" accept
		ct state invalid drop
		ct state established,related accept
		tcp dport { ${PUERTOS//,/, } } accept
		udp sport 67 udp dport 68 accept
		meta l4proto { icmp, ipv6-icmp } accept
	}
	chain salida {
		type filter hook output priority 0; policy drop;
		oif "lo" accept
		# Solo respuestas a conexiones entrantes (SSH, Veyon). Las conexiones
		# salientes abiertas antes del bloqueo quedan cortadas.
		ct direction reply accept
		ip daddr { ${PERMITIDAS//,/, } } accept
		udp dport 67 accept
		icmpv6 type { nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert } accept
		reject with icmpx type admin-prohibited
	}
}
EOF
}

escribir_unidades() {
    local fecha=$1

    cat >"$SYSTEMD/$SERVICIO" <<EOF
[Unit]
Description=Control de Examen: bloqueo de red
DefaultDependencies=no
After=local-fs.target nftables.service
Before=network-pre.target shutdown.target
Wants=network-pre.target
Conflicts=shutdown.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$BIN aplicar
ExecStop=$BIN quitar-tabla

[Install]
WantedBy=sysinit.target
EOF

    cat >"$SYSTEMD/$TIMER" <<EOF
[Unit]
Description=Control de Examen: autodesbloqueo

[Timer]
OnCalendar=$fecha
Persistent=true
AccuracySec=1s
Unit=$SERVICIO_EXPIRA

[Install]
WantedBy=timers.target
EOF

    cat >"$SYSTEMD/$SERVICIO_EXPIRA" <<EOF
[Unit]
Description=Control de Examen: desbloqueo automático

[Service]
Type=oneshot
ExecStart=$BIN desbloquear
EOF

    chmod 0644 "$SYSTEMD/$SERVICIO" "$SYSTEMD/$TIMER" "$SYSTEMD/$SERVICIO_EXPIRA"
}

quitar_tabla() {
    nft delete table inet examen 2>/dev/null || true
}

tabla_activa() {
    nft list table inet examen >/dev/null 2>&1
}

imprimir_estado() {
    if tabla_activa; then
        echo "ESTADO=BLOQUEADO"
        if [[ -r $LIMITE ]]; then
            echo "HASTA=$(date -d "@$(<"$LIMITE")" '+%d/%m %H:%M')"
        fi
    else
        echo "ESTADO=LIBRE"
    fi
}

cmd_bloquear() {
    requiere_root
    leer_opciones "$@"
    [[ $MINUTOS =~ $RE_MINUTOS ]] && ((MINUTOS <= MAX_MINUTOS)) ||
        fallo "minutos de autodesbloqueo no válidos (1-$MAX_MINUTOS)"

    local limite tmp
    limite=$(($(date +%s) + MINUTOS * 60))

    install -d -o root -g root -m 0755 "$DIR"
    tmp=$(mktemp "$DIR/.examen.XXXXXX")
    generar_nft >"$tmp"
    if ! nft -c -f "$tmp"; then
        rm -f "$tmp"
        fallo "las reglas generadas no son válidas"
    fi
    chmod 0644 "$tmp"
    mv -f "$tmp" "$REGLAS"
    echo "$limite" >"$LIMITE"

    escribir_unidades "$(date -d "@$limite" '+%Y-%m-%d %H:%M:%S')"
    systemctl daemon-reload
    systemctl enable --quiet "$SERVICIO" "$TIMER"
    # Aplicar directamente reemplaza la tabla de forma atómica, también al
    # rebloquear con otros parámetros; start no hace nada si ya estaba activo.
    nft -f "$REGLAS"
    systemctl start "$SERVICIO"
    systemctl restart "$TIMER"

    tabla_activa || fallo "la tabla nftables no quedó cargada"
    imprimir_estado
}

cmd_desbloquear() {
    requiere_root
    systemctl disable --now --quiet "$TIMER" "$SERVICIO" 2>/dev/null || true
    quitar_tabla
    rm -f "$SYSTEMD/$SERVICIO" "$SYSTEMD/$TIMER" "$SYSTEMD/$SERVICIO_EXPIRA"
    rm -rf "$DIR"
    systemctl daemon-reload

    tabla_activa && fallo "la tabla nftables sigue cargada"
    imprimir_estado
}

# Arranque (examen-fw.service): recarga el bloqueo salvo que ya haya caducado.
cmd_aplicar() {
    requiere_root
    [[ -r $LIMITE && -r $REGLAS ]] || exit 0
    local limite
    limite=$(<"$LIMITE")
    [[ $limite =~ ^[0-9]+$ ]] || fallo "fichero de límite corrupto"

    if (($(date +%s) >= limite)); then
        quitar_tabla
        rm -rf "$DIR"
        systemctl disable "$SERVICIO" "$TIMER" >/dev/null 2>&1 || true
        echo "ESTADO=LIBRE"
        exit 0
    fi
    nft -f "$REGLAS"
}

main() {
    local accion=${1-}
    (($#)) && shift
    case $accion in
        bloquear) cmd_bloquear "$@" ;;
        desbloquear) cmd_desbloquear ;;
        estado) requiere_root && imprimir_estado ;;
        aplicar) cmd_aplicar ;;
        quitar-tabla) requiere_root && quitar_tabla ;;
        generar-nft)
            leer_opciones "$@"
            generar_nft
            ;;
        *)
            uso >&2
            exit 2
            ;;
    esac
}

main "$@"
