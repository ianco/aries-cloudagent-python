
export DOCKERHOST=${APPLICATION_URL-$(docker run --rm --net=host eclipse/che-ip)}
export LEDGER_URL=http://${DOCKERHOST}:9000
export GENESIS_URL=https://raw.githubusercontent.com/sovrin-foundation/sovrin/stable/sovrin/pool_transactions_builder_genesis

sleep 5

./run_docker start \
 --endpoint http://${DOCKERHOST}:7020 \
 --inbound-transport http 0.0.0.0 7020 \
 --outbound-transport http --admin 0.0.0.0 7021 \
 --label Faber.Agent \
 --auto-provision \
 --auto-ping-connection \
 --auto-respond-messages \
 --auto-accept-invites \
 --auto-accept-requests \
 --admin-insecure-mode \
 --wallet-type indy \
 --wallet-name audit.agent390822 \
 --wallet-key audit.Agent390822 \
 --preserve-exchange-records \
 --plugin aca_py_audit_proof \
 --genesis-url ${GENESIS_URL} \
 --trace-target log \
 --trace-tag acapy.events \
 --trace-label Audit.Agent.trace
