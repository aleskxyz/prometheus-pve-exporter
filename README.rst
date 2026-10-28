Prometheus Proxmox VE Exporter
==============================

|Build Status| |Package Version|

This is an exporter that exposes information gathered from Proxmox VE
node for use by the Prometheus monitoring system.

Installation
------------

Requires Python 3.11 or better.

**Using pip:**

.. code:: shell

    python3 -m pip install prometheus-pve-exporter
    pve_exporter --help

**Using docker:**

.. code:: shell

   docker pull prompve/prometheus-pve-exporter

Running
-------

Example: Display usage message:

.. code:: shell

   docker run -it --rm prompve/prometheus-pve-exporter --help


Example: Run the image with a mounted configuration file and published port:

.. code:: shell

   docker run --init --name prometheus-pve-exporter -d -p 127.0.0.1:9221:9221 -v /path/to/pve.yml:/etc/prometheus/pve.yml prompve/prometheus-pve-exporter

Prometheus PVE Exporter will now be reachable at http://localhost:9221/.

Security
--------

Like other prometheus components, the PVE exporter is subject to the security
threats outlined in the `Prometheus security model`_. Especially:

.. CAUTION::

  The HTTP endpoints provided by Prometheus components should not be exposed to
  publicly accessible networks like the internet (unless you know what you are
  doing and have taken appropriate measures).

Usage
-----

::

    usage: pve_exporter [-h] [--collector.status | --no-collector.status]
                        [--collector.resources | --no-collector.resources]
                        [--collector.node | --no-collector.node]
                        [--collector.cluster | --no-collector.cluster]
                        [--collector.version | --no-collector.version]
                        [--collector.qdevice | --no-collector.qdevice]
                        [--collector.storage-config | --no-collector.storage-config]
                        [--collector.backup-jobs | --no-collector.backup-jobs]
                        [--collector.guest-created | --no-collector.guest-created]
                        [--collector.backup-tasks | --no-collector.backup-tasks]
                        [--collector.subscription | --no-collector.subscription]
                        [--collector.config | --no-collector.config]
                        [--collector.replication | --no-collector.replication]
                        [--collector.resources.sparse-states | --no-collector.resources.sparse-states]
                        [--collector.pve-api-metrics | --no-collector.pve-api-metrics]
                        [--collector.target-metrics | --no-collector.target-metrics]
                        [--config.file CONFIG_FILE]
                        [--web.listen-address WEB_LISTEN_ADDRESS]
                        [--server.keyfile SERVER_KEYFILE]
                        [--server.certfile SERVER_CERTFILE]

    options:
      -h, --help            show this help message and exit
      --config.file CONFIG_FILE
                            Path to config file (/etc/prometheus/pve.yml)
      --web.listen-address WEB_LISTEN_ADDRESS
                            Address on which to expose metrics and web server.
                            ([::]:9221)
      --server.keyfile SERVER_KEYFILE
                            SSL key for server
      --server.certfile SERVER_CERTFILE
                            SSL certificate for server

    cluster collectors:
      cluster collectors are run if the url parameter cluster=1 is set and
      skipped if the url parameter cluster=0 is set on a scrape url.

      --collector.status, --no-collector.status
                            Exposes Node/VM/CT-Status
      --collector.resources, --no-collector.resources
                            Exposes PVE resources info
      --collector.node, --no-collector.node
                            Exposes PVE node info
      --collector.cluster, --no-collector.cluster
                            Exposes PVE cluster info
      --collector.version, --no-collector.version
                            Exposes PVE version info
      --collector.qdevice, --no-collector.qdevice
                            Exposes PVE QDevice connection state
      --collector.storage-config, --no-collector.storage-config
                            Exposes the storage configuration
      --collector.backup-jobs, --no-collector.backup-jobs
                            Exposes backup jobs, the guests they select and their
                            scheduled runs
      --collector.guest-created, --no-collector.guest-created
                            Exposes the creation time of qemu guests (reads each
                            guest config once)
      --collector.backup-tasks, --no-collector.backup-tasks
                            Exposes the newest finished and the running vzdump
                            tasks of each node
      --collector.resources.sparse-states, --no-collector.resources.sparse-states
                            Only exposes pve_guest_ha_state for HA managed guests
                            and pve_guest_lock_state for the lock a guest
                            currently holds

    node collectors:
      node collectors are run if the url parameter node=1 is set and skipped if
      the url parameter node=0 is set on a scrape url.

      --collector.subscription, --no-collector.subscription
                            Exposes PVE subscription info
      --collector.config, --no-collector.config
                            Exposes PVE onboot status
      --collector.replication, --no-collector.replication
                            Exposes PVE replication info

    scrape collectors:
      metrics concerning the operation of the Prometheus PVE exporter itself.

      --collector.pve-api-metrics, --no-collector.pve-api-metrics
                            Exposes duration and errors of PVE API requests per
                            endpoint
      --collector.target-metrics, --no-collector.target-metrics
                            Exposes duration of scrapes by target


Use `[::]` in the `--web.listen-address` flag in order to bind to both IPv6 and
IPv4 sockets on dual stacked machines.

Visit http://localhost:9221/pve?target=1.2.3.4&cluster=1&node=1 where 1.2.3.4
is the IP of the Proxmox VE node to get metrics from. Specify the ``module``
request parameter, to choose which module to use from the config file.

The ``target`` request parameter defaults to ``localhost``. Hence if
``pve_exporter`` is deployed directly on the proxmox host, ``target``
can be omitted.

Use the `--collector.X` / `--no-collector.X` flags to enable disable selected
collectors.

Note that that the config collector results in one API call per guest VM/CT.
It is therefore recommended to disable this collector using the
`--no-collector.config` flag on big deployments.

Scrape collectors return metrics concerning the operation of the Prometheus PVE
exporter itself. Those metrics are available from the `/metrics`.

See the wiki_  for more examples and docs.

Exported Metrics
----------------

Here's an example of the metrics exported.

::

    # HELP pve_node_online Whether the node is online (/cluster/status).
    # TYPE pve_node_online gauge
    pve_node_online{id="node/proxmox"} 1.0
    # HELP pve_cluster_quorate Whether the cluster is quorate (/cluster/status).
    # TYPE pve_cluster_quorate gauge
    pve_cluster_quorate{id="cluster/pvc"} 1.0
    # HELP pve_guest_running Whether the guest is running (status of /cluster/resources).
    # TYPE pve_guest_running gauge
    pve_guest_running{id="qemu/100"} 1.0
    # HELP pve_storage_available Whether the storage is available on the node (status of /cluster/resources).
    # TYPE pve_storage_available gauge
    pve_storage_available{id="storage/proxmox/local"} 1.0
    # HELP pve_node_cpu_usage_ratio CPU utilization of the node.
    # TYPE pve_node_cpu_usage_ratio gauge
    pve_node_cpu_usage_ratio{id="node/proxmox"} 0.034
    # HELP pve_node_cpus Number of CPUs of the node.
    # TYPE pve_node_cpus gauge
    pve_node_cpus{id="node/proxmox"} 32.0
    # HELP pve_node_memory_usage_bytes Used memory of the node.
    # TYPE pve_node_memory_usage_bytes gauge
    pve_node_memory_usage_bytes{id="node/proxmox"} 5.39e+10
    # HELP pve_node_memory_size_bytes Memory of the node.
    # TYPE pve_node_memory_size_bytes gauge
    pve_node_memory_size_bytes{id="node/proxmox"} 6.74e+10
    # HELP pve_node_rootfs_usage_bytes Used space of the root file system of the node.
    # TYPE pve_node_rootfs_usage_bytes gauge
    pve_node_rootfs_usage_bytes{id="node/proxmox"} 2.1e+10
    # HELP pve_node_rootfs_size_bytes Size of the root file system of the node.
    # TYPE pve_node_rootfs_size_bytes gauge
    pve_node_rootfs_size_bytes{id="node/proxmox"} 1.01e+11
    # HELP pve_node_uptime_seconds Uptime of the node.
    # TYPE pve_node_uptime_seconds gauge
    pve_node_uptime_seconds{id="node/proxmox"} 987654.0
    # HELP pve_guest_cpu_usage_ratio CPU utilization of the guest.
    # TYPE pve_guest_cpu_usage_ratio gauge
    pve_guest_cpu_usage_ratio{id="qemu/100"} 0.012
    # HELP pve_guest_cpus Number of virtual CPUs of the guest.
    # TYPE pve_guest_cpus gauge
    pve_guest_cpus{id="qemu/100"} 4.0
    # HELP pve_guest_memory_usage_bytes Used memory of the guest.
    # TYPE pve_guest_memory_usage_bytes gauge
    pve_guest_memory_usage_bytes{id="qemu/100"} 1.66e+10
    # HELP pve_guest_memory_size_bytes Memory of the guest.
    # TYPE pve_guest_memory_size_bytes gauge
    pve_guest_memory_size_bytes{id="qemu/100"} 1.72e+10
    # HELP pve_guest_disk_usage_bytes Used space of the root file system of a container; not exposed for VMs, as PVE can not see inside them and reports 0.
    # TYPE pve_guest_disk_usage_bytes gauge
    # HELP pve_guest_disk_size_bytes Size of the root disk of the guest.
    # TYPE pve_guest_disk_size_bytes gauge
    pve_guest_disk_size_bytes{id="qemu/100"} 1.07e+11
    # HELP pve_guest_uptime_seconds Uptime of the guest.
    # TYPE pve_guest_uptime_seconds gauge
    pve_guest_uptime_seconds{id="qemu/100"} 3600.0
    # HELP pve_guest_network_receive_bytes_total Bytes the guest received over the network since it was started.
    # TYPE pve_guest_network_receive_bytes_total counter
    pve_guest_network_receive_bytes_total{id="qemu/100"} 1.53e+09
    # HELP pve_guest_network_transmit_bytes_total Bytes the guest sent over the network since it was started.
    # TYPE pve_guest_network_transmit_bytes_total counter
    pve_guest_network_transmit_bytes_total{id="qemu/100"} 7.75e+09
    # HELP pve_guest_disk_read_bytes_total Bytes the guest read from its block devices since it was started (not for all storage types).
    # TYPE pve_guest_disk_read_bytes_total counter
    pve_guest_disk_read_bytes_total{id="qemu/100"} 7.47e+09
    # HELP pve_guest_disk_written_bytes_total Bytes the guest wrote to its block devices since it was started (not for all storage types).
    # TYPE pve_guest_disk_written_bytes_total counter
    pve_guest_disk_written_bytes_total{id="qemu/100"} 1.5e+11
    # HELP pve_storage_usage_bytes Used space of the storage on the node.
    # TYPE pve_storage_usage_bytes gauge
    pve_storage_usage_bytes{id="storage/proxmox/local"} 2.1e+10
    # HELP pve_storage_size_bytes Size of the storage on the node.
    # TYPE pve_storage_size_bytes gauge
    pve_storage_size_bytes{id="storage/proxmox/local"} 1.01e+11
    # HELP pve_storage_shared Whether the storage is shared among cluster nodes.
    # TYPE pve_storage_shared gauge
    pve_storage_shared{id="storage/proxmox/local"} 0.0
    # HELP pve_node_ha_state HA state of the node.
    # TYPE pve_node_ha_state gauge
    pve_node_ha_state{id="node/proxmox",state="online"} 1.0
    pve_node_ha_state{id="node/proxmox",state="maintenance"} 0.0
    pve_node_ha_state{id="node/proxmox",state="unknown"} 0.0
    pve_node_ha_state{id="node/proxmox",state="fence"} 0.0
    pve_node_ha_state{id="node/proxmox",state="gone"} 0.0
    # HELP pve_guest_ha_state HA state of the guest (for HA managed guests).
    # TYPE pve_guest_ha_state gauge
    pve_guest_ha_state{id="qemu/100",state="stopped"} 0.0
    pve_guest_ha_state{id="qemu/100",state="request_stop"} 0.0
    pve_guest_ha_state{id="qemu/100",state="request_start"} 0.0
    pve_guest_ha_state{id="qemu/100",state="request_start_balance"} 0.0
    pve_guest_ha_state{id="qemu/100",state="started"} 1.0
    pve_guest_ha_state{id="qemu/100",state="fence"} 0.0
    pve_guest_ha_state{id="qemu/100",state="recovery"} 0.0
    pve_guest_ha_state{id="qemu/100",state="migrate"} 0.0
    pve_guest_ha_state{id="qemu/100",state="relocate"} 0.0
    pve_guest_ha_state{id="qemu/100",state="freeze"} 0.0
    pve_guest_ha_state{id="qemu/100",state="error"} 0.0
    # HELP pve_guest_lock_state The guest's current config lock
    # TYPE pve_guest_lock_state gauge
    pve_guest_lock_state{id="qemu/100",state="backup"} 1.0
    # HELP pve_guest_info VM/CT info
    # TYPE pve_guest_info gauge
    pve_guest_info{id="qemu/100",name="samplevm1",node="proxmox",pool="prod",tags="tag1;tag2",template="0",type="qemu",vmid="100"} 1.0
    # HELP pve_storage_info Storage info
    # TYPE pve_storage_info gauge
    pve_storage_info{content="backup,iso,vztmpl",id="storage/proxmox/local",node="proxmox",plugintype="dir",storage="local"} 1.0
    # HELP pve_node_info Node info
    # TYPE pve_node_info gauge
    pve_node_info{id="node/proxmox",level="c",name="proxmox",nodeid="1"} 1.0
    # HELP pve_cluster_info Cluster info (version of the cluster configuration), always 1.
    # TYPE pve_cluster_info gauge
    pve_cluster_info{id="cluster/pvc",version="2"} 1.0
    # HELP pve_cluster_nodes Number of nodes of the cluster.
    # TYPE pve_cluster_nodes gauge
    pve_cluster_nodes{id="cluster/pvc"} 1.0
    # HELP pve_version_info Proxmox VE version info
    # TYPE pve_version_info gauge
    pve_version_info{release="9.2",repoid="6fe299a0",version="9.2.13"} 1.0
    # HELP pve_subscription_info Proxmox VE subscription info (1 if present)
    # TYPE pve_subscription_info gauge
    pve_subscription_info{id="node/proxmox",level="c"} 1.0
    # HELP pve_subscription_status Proxmox VE subscription status (1 if matches status)
    # TYPE pve_subscription_status gauge
    pve_subscription_status{id="node/proxmox",status="new"} 0.0
    pve_subscription_status{id="node/proxmox",status="notfound"} 0.0
    pve_subscription_status{id="node/proxmox",status="active"} 1.0
    pve_subscription_status{id="node/proxmox",status="invalid"} 0.0
    pve_subscription_status{id="node/proxmox",status="expired"} 0.0
    pve_subscription_status{id="node/proxmox",status="suspended"} 0.0
    # HELP pve_subscription_next_due_timestamp_seconds Subscription next due date as Unix timestamp (midnight UTC)
    # TYPE pve_subscription_next_due_timestamp_seconds gauge
    pve_subscription_next_due_timestamp_seconds{id="node/proxmox"} 1.713312e+09
    # HELP pve_qdevice_up Proxmox VE QDevice is connected (1) or not (0)
    # TYPE pve_qdevice_up gauge
    pve_qdevice_up{id="cluster/pvc"} 1.0
    # HELP pve_qdevice_info Proxmox VE QDevice info (1 if configured)
    # TYPE pve_qdevice_info gauge
    pve_qdevice_info{id="cluster/pvc",model="Net",algorithm="Fifty-Fifty split",qnetd_host="10.0.0.1:5403",tie_breaker="Node with lowest node ID"} 1.0
    # HELP pve_guest_onboot Whether the guest starts at boot (onboot config option, 0 if not set).
    # TYPE pve_guest_onboot gauge
    pve_guest_onboot{id="qemu/201",node="proxmox"} 1.0
    # HELP pve_collector_success Whether a collector succeeded in this scrape.
    # TYPE pve_collector_success gauge
    pve_collector_success{collector="backup_jobs"} 1.0
    # HELP pve_replication_duration_seconds Proxmox vm replication duration
    # TYPE pve_replication_duration_seconds gauge
    pve_replication_duration_seconds{id="1-0"} 7.73584
    # HELP pve_replication_last_sync_timestamp_seconds Proxmox vm replication last_sync
    # TYPE pve_replication_last_sync_timestamp_seconds gauge
    pve_replication_last_sync_timestamp_seconds{id="1-0"} 1.713382503e+09
    # HELP pve_replication_last_try_timestamp_seconds Proxmox vm replication last_try
    # TYPE pve_replication_last_try_timestamp_seconds gauge
    pve_replication_last_try_timestamp_seconds{id="1-0"} 1.713382503e+09
    # HELP pve_replication_next_sync_timestamp_seconds Proxmox vm replication next_sync
    # TYPE pve_replication_next_sync_timestamp_seconds gauge
    pve_replication_next_sync_timestamp_seconds{id="1-0"} 1.7134689e+09
    # HELP pve_replication_failed_syncs Proxmox vm replication fail_count
    # TYPE pve_replication_failed_syncs gauge
    pve_replication_failed_syncs{id="1-0"} 0.0
    # HELP pve_replication_info Proxmox vm replication info
    # TYPE pve_replication_info gauge
    pve_replication_info{guest="qemu/1",id="1-0",source="node/proxmox1",target="node/proxmox2",type="local"} 1.0

Authentication
--------------

**Using pve.yml config file**

Example ``pve.yml`` for password authentication:

.. code:: yaml

    default:
        user: prometheus@pve
        password: sEcr3T!
        # Optional: set to false to skip SSL/TLS verification
        verify_ssl: true

Example ``pve.yml`` for `token authentication`_:

.. code:: yaml

   default:
       user: prometheus@pve
       token_name: "your-token-id"
       token_value: "..."

**Using environment variables:**

If the ``PVE_USER`` environment variable exists, then configuration is taken from
the environment instead of from the ``pve.yml`` config file. The following
environment variables are respected:

* ``PVE_USER``: user name

Required for password authentication:

* ``PVE_PASSWORD``: user password

Required for `token authentication`_:

* ``PVE_TOKEN_NAME``: token name
* ``PVE_TOKEN_VALUE``: token value

Optional:

* ``PVE_VERIFY_SSL``: Either ``true`` or ``false`` (also ``1``/``0``, ``yes``/``no``,
  ``on``/``off``), whether or not to verify PVE tls certificate. Defaults to ``true``,
  also when empty.
* ``PVE_MODULE``: Name of the configuration module. Defaults to ``default``.
* ``PVE_ALLOW_LIST``: Comma separated list of allowed targets, e.g.
  ``pve1.example.com,pve2.example.com``. See below.

The configuration is passed directly into `proxmoxer.ProxmoxAPI()`_, except
for the ``allow_list`` key.

**Restricting targets with allow_list:**

The ``target`` request parameter decides which host the module's credentials
are sent to. Anyone who can reach the exporter could otherwise point
``target`` at a host they control and capture the PVE password or API token.
To prevent this, list the PVE hosts a module may be used with:

.. code:: yaml

   default:
       user: prometheus@pve
       token_name: "your-token-id"
       token_value: "..."
       allow_list:
         - pve1.example.com
         - pve2.example.com
         - 10.0.0.5:8006

Requests for any other target are rejected with HTTP 403 and no connection is
made. Entries are compared to the ``target`` parameter exactly (ignoring case
and surrounding whitespace), so ``host`` and ``host:port`` are different
entries. Note that ``target`` defaults to ``localhost``, which has to be listed
too if scrapes omit the parameter. Modules without ``allow_list`` accept any
target, and a warning is logged on startup.

**Note on verify_ssl and certificate trust store:**

When operating PVE with self-signed certificates, then it is necessary to
either import the certificate into the local trust store (see this `SE answer`_
for Debian/Ubuntu) or add ``verify_ssl: false`` to the config dict as a sibling
to the credentials. Note that PVE `supports Let's Encrypt`_ out ouf the box. In
many cases setting up trusted certificates is the better option than operating
with self-signed certs.

Proxmox VE Configuration
------------------------

For security reasons it is essential to add a user with read-only access
(PVEAuditor role) for the purpose of metrics collection.

Refer to the  `Proxmox Documentation`_ for the several ways of creating a user.
Once created, assign the user the `/` path permission.

Collector failures
------------------

A collector that fails (e.g. for a missing privilege) does not fail the
scrape: its metrics are left out and ``pve_collector_success{collector}`` is 0.
A collector that reads several objects (backup jobs, guest configs, node task
lists) and fails for some exports the others, also reports 0 and logs one
warning naming them. A target that does not answer the version call fails the
scrape (HTTP 500, so ``up`` is 0); after a request fails to reach the target,
the rest of the scrape fails at once instead of waiting for each timeout. Each
API request is sent once per scrape, also when it fails, so a slow endpoint
costs its timeout once. What the exporter keeps per target is forgotten a day
after the target's last scrape.

Backup metrics
--------------

``backup-jobs`` (on by default) reads ``/cluster/backup``:

- ``pve_backup_job_info{job_id,storage,schedule,mode,node,all,pool,repeat_missed}``
  (the job's own keys), ``pve_backup_job_enabled{job_id}`` and
  ``pve_backup_job_next_run_timestamp_seconds{job_id}`` (``next-run``).
- ``pve_backup_job_{previous,last}_scheduled_timestamp_seconds{job_id}``: the two
  newest runs not in the future that ``/cluster/jobs/schedule-analyze`` returns
  for an enabled job's schedule, so PVE's calendar logic applies. Compare
  backups against these instead of an assumed interval; they are exact for
  irregular schedules such as ``mon..fri 21:00``.
- ``pve_backup_job_guest_info{job_id,storage,id,vmid}``: the guests a job selects,
  as PVE resolves ``all``, ``pool``, ``vmid``, ``exclude`` and ``node``
  (``included_volumes``). ``pve_backup_job_guest_excluded_volumes{job_id,id}``
  counts the volumes it leaves out (only guests with excluded volumes),
  ``pve_backup_job_unknown_guest_info{job_id,vmid}`` lists VMIDs a job names that
  are no guests. A job whose guests or schedule can not be read is left out as
  a whole.

``guest-created`` (on by default):
``pve_guest_created_timestamp_seconds{id}``, the creation time of every qemu guest
(``ctime`` of its ``meta`` config option; LXC has none). Each guest's config is
read once and cached (read again when the VMID gets another name); guests of
offline nodes are skipped. A scrape starts no config read after 5 seconds; the
rest follows in the next scrapes, and the collector reports 0 until all are
read.

``storage-config`` (on by default) reads ``/storage``:
``pve_storage_config_info{storage,type,server,port,datastore,datastore_namespace,fingerprint,disabled}``.
For a Proxmox Backup Server storage, ``fingerprint``, ``datastore`` and
``datastore_namespace`` tell where its backups are.

``backup-tasks`` (off by default) reads the vzdump tasks of every online node from
the node task list (``/nodes/{node}/tasks?typefilter=vzdump``), one series per node:
``pve_backup_task_last_{start,end}_timestamp_seconds`` and ``pve_backup_task_last_success``
of the newest finished task, ``pve_backup_tasks_running`` (tasks waiting for the
backup lock count as running) and, while one runs,
``pve_backup_task_running_start_timestamp_seconds``. The task list tells neither
the storage nor the guests of a task, and task logs are not read: whether a
guest was backed up is for the backup storage to tell (e.g. the snapshots a
Proxmox Backup Server reports).

A token with the ``PVEAuditor`` role on ``/`` has all privileges these need.

Prometheus Configuration
------------------------

The PVE exporter can be deployed either directly on a Proxmox VE node or
onto a separate machine.

Example config for PVE exporter running on PVE node:

.. code:: yaml

    scrape_configs:
      - job_name: 'pve'
        static_configs:
          - targets:
            - 192.168.1.2:9221  # Proxmox VE node with PVE exporter.
            - 192.168.1.3:9221  # Proxmox VE node with PVE exporter.
        metrics_path: /pve
        params:
          module: [default]
          cluster: ['1']
          node: ['1']

Example config for PVE exporter running on Prometheus host:

.. code:: yaml

    scrape_configs:
      - job_name: 'pve'
        static_configs:
          - targets:
            - 192.168.1.2  # Proxmox VE node.
            - 192.168.1.3  # Proxmox VE node.
        metrics_path: /pve
        params:
          module: [default]
          cluster: ['1']
          node: ['1']
        relabel_configs:
          - source_labels: [__address__]
            target_label: __param_target
          - source_labels: [__param_target]
            target_label: instance
          - target_label: __address__
            replacement: 127.0.0.1:9221  # PVE exporter.

**Note on alerting:**

You can do VM tag based alerting, by using joins on ``pve_guest_info`` metric. For
example, alerting only when VM with `critical` tag is down:

.. code:: promql

   (pve_guest_info{tags=~".*critical.*"} * on(id) group_left(name) pve_guest_running) == 0

**Note on scraping large clusters:**

It is adviced to setup separate jobs to collect ``cluster`` metrics and
``node`` metrics in larger deployments. Scraping any node in a cluster with the
url params set to ``cluster=1&node=0`` results in the same set of metrics. Hence
cluster metrics can be scraped efficiently from a single node or from a subset
of cluster nodes (e.g., a different node selected on every scrape via
round-robin DNS).

Node metrics can only be scraped from a given node. In order to compile a
complete set of node metrics it is necessary to scrape every node in a cluster
with url params set to ``cluster=0&node=1``.


Grafana Dashboards
------------------

* `Proxmox via Prometheus by Pietro Saccardi`_

.. |Build Status| image:: https://github.com/prometheus-pve/prometheus-pve-exporter/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/prometheus-pve/prometheus-pve-exporter/actions/workflows/ci.yml
.. |Package Version| image:: https://img.shields.io/pypi/v/prometheus-pve-exporter.svg
   :target: https://pypi.python.org/pypi/prometheus-pve-exporter
.. _`Prometheus security model`: https://prometheus.io/docs/operating/security/
.. _wiki: https://github.com/prometheus-pve/prometheus-pve-exporter/wiki
.. _`token authentication`: https://pve.proxmox.com/wiki/User_Management#pveum_tokens
.. _`proxmoxer.ProxmoxAPI()`: https://pypi.python.org/pypi/proxmoxer
.. _`SE answer`: https://askubuntu.com/a/1007236
.. _`supports Let's Encrypt`: https://pve.proxmox.com/pve-docs/pve-admin-guide.html#sysadmin_certificate_management
.. _`Proxmox Documentation`: https://pve.proxmox.com/pve-docs/pve-admin-guide.html#pveum_permission_management
.. _`Proxmox via Prometheus by Pietro Saccardi`: https://grafana.com/grafana/dashboards/10347-proxmox-via-prometheus/
