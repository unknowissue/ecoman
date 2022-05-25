import datetime
import ipaddress
import json
import pathlib
import re
from dataclasses import dataclass
from importlib.resources import path
from typing import Dict, Tuple

import paramiko
from paramiko import channel

import load_config
import vim_cmd_parser


class PowerStatus:
    ON: str = "on"
    OFF: str = "off"
    SUSPEND: str = "suspend"
    UNKNOWN: str = "unknown"


""" Init ssh connecter """
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.load_system_host_keys()


# def slack_notify(message):
#     """ Slack通知 """
#     import requests
#     import os
#     SLACK_WEBHOOK = os.environ.get('SLACK_WEBHOOK')
#     if SLACK_WEBHOOK:
#         requests.post(SLACK_WEBHOOK, data=json.dumps({
#             'text': message,  # 投稿するテキスト
#         }))


@dataclass
class MachineDetail:
    id: int
    name: str
    datastore: str
    datastore_path: pathlib.Path
    guest_os: str
    vm_version: str
    comment: str


def get_vms_list() -> Dict[int, MachineDetail]:
    """ VMのリストを取得 """

    # VM情報一覧の2行目～を取得(ラベルを除外)
    _, stdout, _ = client.exec_command('vim-cmd vmsvc/getallvms')
    vm_info: Dict[int, MachineDetail] = {}
    for line in stdout.readlines():
        # 数字から始まる行
        if re.match(r'^\d+', line):
            dat = line.strip('\n').split()
            vmid = int(dat[0])
            vm_info[vmid] = MachineDetail(
                id=vmid,
                name=dat[1],
                datastore=dat[2],
                datastore_path=dat[3],
                guest_os=dat[4],
                vm_version=dat[5],
                comment=' '.join(dat[6:])
            )

        # Vmidから始まる行
        elif line.startswith("Vmid"):
            continue

    return vm_info


def get_vms_power() -> Dict[int, PowerStatus]:
    """ VMの電源状態のリストを取得 """

    # VMの電源一覧を取得
    _, stdout, _ = client.exec_command(r"""
    for id in `vim-cmd vmsvc/getallvms | grep '^[0-9]\+' | awk '{print $1}'`
    do
      vim-cmd vmsvc/power.getstate $id | grep -v Retrieved | sed "s/^/$id|/g" &
    done
    """)

    # VMの電源一覧を整形
    result: Dict[int, PowerStatus] = {}
    for line in stdout.readlines():
        _vmid, state = line.split('|')
        vmid = int(_vmid)
        if 'Suspended' in state:
            result[vmid] = PowerStatus.SUSPEND
        elif 'Powered on' in state:
            result[vmid] = PowerStatus.ON
        elif 'Powered off' in state:
            result[vmid] = PowerStatus.OFF
        else:
            result[vmid] = PowerStatus.UNKNOWN

    return result


def get_vms_ip() -> Dict[int, ipaddress.IPv4Address]:
    """ VMのIPアドレスのリストを取得 """

    _, stdout, stderr = client.exec_command(r"""
    for id in `vim-cmd vmsvc/getallvms | grep '^[0-9]\+' | awk '{print $1}'`
    do
      vim-cmd vmsvc/get.summary $id | grep ipAddress | grep -o \"[0-9a-f:\.]\\+\" | sed "s/\"//g;s/^/$id|/g" &
    done
    """)

    result: Dict[int, ipaddress.IPv4Address] = {}
    for line in stdout.readlines():
        vmid, ipaddr = line.split('|')
        result[int(vmid)] = ipaddress.IPv4Address(ipaddr)

    return result


def get_vm_detail(esxi_nodename: str, vmid: int):
    """ 個別VMの詳細を取得 """

    hostinfo = load_config.get_esxi_nodes().get(esxi_nodename)
    if hostinfo is None:
        return "error"

    client.connect(
        hostname=hostinfo.get('addr'),
        username=hostinfo.get('username'),
        password=hostinfo.get('password')
    )
    _, stdout, _ = client.exec_command(f'vim-cmd vmsvc/get.summary {vmid}')
    vm_detail = vim_cmd_parser.parser(stdout.read().decode().split('\n'))
    # TODO: vm_detailが空か判定する
    try:
        info_tag = re.search(
            r'<info>.*</info>', vm_detail['vim.vm.Summary']['config']['annotation'])
    except KeyError:
        info_tag = None

    # Don't have json data
    if info_tag is None:
        vm_org_info = {}
        annotation = ''
    else:
        json_str = info_tag.group().strip('<info>').strip('</info>')
        vm_org_info = json.loads(json_str)
        annotation = re.sub(r'<info>.*</info>', '',
                            vm_detail['vim.vm.Summary']['config']['annotation'])

    def format_func(x): return '' if x is None else x
    vm_detail['info'] = {
        'author': format_func(vm_org_info.get('author')),
        'user': format_func(vm_org_info.get('user')),
        'password': format_func(vm_org_info.get('password')),
        'created_at': format_func(vm_org_info.get('created_at')),
        'tag': ', '.join(format_func(vm_org_info.get('tag'))),
        'annotation': format_func(annotation)
    }
    return vm_detail


def set_vm_power(esxi_nodename: str, vmid: int, power_state: PowerStatus) -> str:
    """ 個別VMの電源を操作 """

    host = load_config.get_esxi_nodes().get(esxi_nodename)
    assert host is not None, "Undefined uniq_id."
    POWER_STATE = ('on', 'off', 'shutdown', 'reset', 'reboot', 'suspend')
    assert power_state in POWER_STATE, "Invalid power state."

    client.connect(
        hostname=host.get('addr'),
        username=host.get('username'),
        password=host.get('password')
    )
    _, stdout, _ = client.exec_command(
        f'vim-cmd vmsvc/power.{power_state} {vmid}')
    # TODO: 判定を作成
    '''
    ON) Powering on VM:
    SHUTDOWN) 空
    OFF) Powering off VM:
    RESET) Reset VM:
    REBOOT) 空
    SUSPEND) Suspending VM:
    '''
    # client.close()
    return stdout.read().decode().strip('\n')


def create_vm(
    name: str,  # 'ecoman-example3'
    ram_mb: int,  # 512
    cpu_cores: int,  # 1
    storage_gb: int,  # 30
    network_port_group: str,  # "private"
    store_path: pathlib.Path,  # "/vmfs/volumes/StoreNAS-Jasmine/"
    iso_path: pathlib.Path,  # "/vmfs/volumes/StoreNAS-Public/xxx.iso"
    esxi_nodename: str,  # "jasmine"
    comment: str
) -> Tuple[channel.ChannelFile, channel.ChannelStderrFile]:
    """ VMを作成 """

    hostinfo = load_config.get_esxi_nodes().get(esxi_nodename)
    client.connect(
        hostname=hostinfo.get('addr'),
        username=hostinfo.get('username'),
        password=hostinfo.get('password')
    )

    # dt_now = datetime.datetime.now()
    # CUR_DATE = dt_now.strftime('%Y-%m-%d %H:%M:%S')

    concat_payload = comment
    cmd = f"""
    vmid=`vim-cmd vmsvc/createdummyvm {name} {store_path}`
    cd {store_path}{name}
    sed -i -e '/^guestOS =/d' {name}.vmx

    cat << EOF >> {name}.vmx
guestOS = "ubuntu-64"
memsize = "{ram_mb}"
numvcpus = "{cpu_cores}"
ethernet0.addressType = "generated"
ethernet0.generatedAddressOffset = "0"
ethernet0.networkName = "{network_port_group}"
ethernet0.pciSlotNumber = "160"
ethernet0.present = "TRUE"
ethernet0.uptCompatibility = "TRUE"
ethernet0.virtualDev = "vmxnet3"
ethernet0.wakeOnPcktRcv = "TRUE"
powerType.powerOff = "default"
powerType.reset = "default"
powerType.suspend = "soft"
sata0.present = "TRUE"
sata0:0.deviceType = "cdrom-image"
sata0:0.fileName = "{iso_path}"
sata0:0.present = "TRUE"
annotation = "{concat_payload}"
EOF

    rm {name}-flat.vmdk  {name}.vmdk
    vmkfstools --createvirtualdisk {storage_gb}G -d thin {name}.vmdk
    vim-cmd vmsvc/reload $vmid
    vim-cmd vmsvc/power.on $vmid
    """

    # print(cmd)
    _, stdout, stderr = client.exec_command(cmd)
    return stdout, stderr


def api_create_vm(specs: MachineSpec):
    # NAME
    if specs.name:
        vm_name = specs.name
    else:
        import random
        suffix = str(random.randint(0, 999)).zfill(3)
        vm_name = f"machine-{suffix}"

    # RAM
    if specs.ram_mb >= 512:
        vm_ram_mb = specs.ram_mb
    else:
        vm_ram_mb = 512

    # CPU
    if specs.cpu_cores >= 1:
        vm_cpu = specs.cpu_cores
    else:
        vm_cpu = 1

    # Storage
    if specs.storage_gb >= 30:
        vm_storage_gb = specs.storage_gb
    else:
        vm_storage_gb = 30

    # Network
    if specs.network_port_group:
        vm_network_name = specs.network_port_group
    else:
        vm_network_name = "VM Network"

    # ESXi Node
    conf = load_config.get_esxi_nodes()
    allow_nodes = tuple(conf.keys())
    if specs.get('esxi_node') and specs.get('esxi_node') in allow_nodes:
        esxi_node_name = specs.get('esxi_node')
    else:
        esxi_node_name = "jasmine"

    # StorePath
    # store_path_node = str.capitalize(esxi_node_name)
    # vm_store_path = f"/vmfs/volumes/StoreNAS-{store_path_node}/"
    # vm_store_path = f"/vmfs/volumes/datastore1/"
    nodename = specs['esxi_node']
    vm_store_path = conf[nodename]['datastore_path']

    # ISO Path
    # vm_iso_path = "/vmfs/volumes/StoreNAS-Public/os-images/custom/ubuntu-18.04.4-server-amd64-preseed.20190824.040414.iso"
    vm_iso_path = "/vmfs/volumes/koyama-auto-install/ubuntu-2004-auto-20220105.iso"

    # Tags
    if specs.get('tags'):
        tags = specs.get('tags')
    else:
        tags = []

    # Comment
    if specs.get('comment') and len(specs.get('comment')) > 0:
        comment = specs.get('comment')
    else:
        comment = ""

    # Author
    if specs.get('author') and len(specs.get('author')) > 3:
        author = specs.get('author')
    else:
        author = "anonymous"

    stdout, stderr = create_vm(vm_name, vm_ram_mb, vm_cpu, vm_storage_gb,
                               vm_network_name, vm_store_path, vm_iso_path, esxi_node_name,
                               author, tags, comment)
    stdout_lines = stdout.readlines()
    stderr_lines = stderr.readlines()

    if len(stderr_lines) > 0:
        payload = ' '.join([line.strip() for line in stderr_lines])
        # slack_notify(f"[Error] {author} created {vm_name}. detail: {payload}")
        return {"error": payload}
    else:
        payload = ' '.join([line.strip() for line in stdout_lines])
        # slack_notify(
        #     f"[Success] {author} created {vm_name}. detail: {payload}")
        return {"status": payload}


def main():
    pass


if __name__ == '__main__':
    main()
