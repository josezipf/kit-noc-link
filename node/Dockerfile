FROM alpine:3.22
RUN apk add --no-cache iproute2 iproute2-tc net-snmp net-snmp-tools iperf3 tini python3 frr frr-snmp
COPY entrypoint.sh /usr/local/bin/lab-node
COPY ifalias-pass.sh /usr/local/bin/ifalias-pass
COPY trafego.sh /usr/local/bin/lab-trafego
COPY provedor.py /usr/local/bin/lab-provedor
COPY olt_sim.py /usr/local/bin/lab-olt-sim
RUN chmod 755 /usr/local/bin/lab-node /usr/local/bin/ifalias-pass /usr/local/bin/lab-trafego /usr/local/bin/lab-provedor /usr/local/bin/lab-olt-sim
ENV SNMP_PERSISTENT_DIR=/tmp/net-snmp
ENTRYPOINT ["/usr/local/bin/lab-node"]
