import paramiko
import asyncio

import vim_cmd_parser


""" Init ssh connecter """
client = paramiko.SSHClient()
client.load_system_host_keys()


""" ESXi一覧をファイルから取得 """
def get_esxi_hosts():
    import yaml
    with open("hosts.yaml") as f:
        return yaml.safe_load(f.read())


# """ ESXiのhost->addrを解決 """
# def app_resolve_esxi_addr(esxi_host):
#     esxi_dct = get_esxi_hosts()
#     try:
#         esxi_addr = esxi_dct[esxi_host]['addr']
#     except KeyError:
#         raise ValueError("Cloud not resolve host. Given undefined host.")
#     return esxi_addr


""" VMのリストを取得 """
def get_vms_list():
    import re
    import json
    # VM情報一覧の2行目～を取得(ラベルを除外)
    stdin, stdout, stderr = client.exec_command('vim-cmd vmsvc/getallvms')
    vm_info = []
    for line in stdout.readlines():
        # 数字から始まる行
        if re.match(r'^\d+', line):
            # 前の要素を更新
            if len(vm_info) > 1:
                vm_memo = re.search(r'<info>.+</info>', vm_info[-1]['comment'], flags=re.DOTALL)
                # <info>xxx</info>を含む
                if vm_memo is not None:
                    json_str = vm_memo.group().strip('<info>').strip('</info>').strip('\n')
                    print(json_str)
                    vm_info[-1]['memo'] = json.loads(json_str)
                    # comment属性の<info>xxx</info>を取り除く
                    vm_info[-1]['comment'] = re.sub(r'<info>.*</info>', '', vm_info[-1]['comment'], flags=re.DOTALL)

            dat = line.strip('\n').split()
            vm_info.append({
                'id': dat[0],
                'name': dat[1],
                'datastore': dat[2],
                'datastore_path': dat[3],
                'guest_os': dat[4],
                'vm_version': dat[5],
                'comment': ' '.join(dat[6:]),
                'memo': None
            })
        elif line.startswith("Vmid"):
            continue
        else:
            vm_info[-1]['comment'] += line

    return vm_info


""" VMの電源状態のリストを取得 """
def get_vms_power():
    # VMの電源一覧を取得
    stdin, stdout, stderr = client.exec_command("""
    for id in `vim-cmd vmsvc/getallvms | sed '/^Vmid.*$/d' | awk '{print $1}'`
    do
      vim-cmd vmsvc/power.getstate $id | grep -v Retrieved | sed "s/^/$id|/g" &
    done
    """)

    # VMの電源一覧を整形
    result = {}
    for line in stdout.readlines():
        vmid, state = line.split('|')
        if 'Suspended' in state:
            result[vmid] = 'suspend'
        elif 'Powered on' in state:
            result[vmid] = 'on'
        elif 'Powered off' in state:
            result[vmid] = 'off'
        else:
            result[vmid] = 'unknown'

    return result


""" 個別VMの詳細を取得 """
def get_vm_detail(esxi_hostname, vmid):
    hostinfo = get_esxi_hosts().get(esxi_hostname)
    if hostinfo is None:
        return "error"
    client.connect(
        hostname=hostinfo.get('addr'),
        username=hostinfo.get('username'),
        password=hostinfo.get('password')
    )
    stdin, stdout, stderr = client.exec_command(f'vim-cmd vmsvc/get.summary {vmid}')
    # client.close()
    return vim_cmd_parser.parser(stdout.read().decode().split('\n'))


""" 個別VMの電源を操作 """
def set_vm_power(esxi_hostname, vmid, power_state):
    host = get_esxi_hosts().get(esxi_hostname)
    assert host is not None, "Undefined uniq_id."
    POWER_STATE = ('on', 'off', 'shutdown', 'reset', 'reboot', 'suspend')
    assert power_state in POWER_STATE, "Invalid power state."
    
    client.connect(
        hostname=host.get('addr'),
        username=host.get('username'),
        password=host.get('password')
    )
    stdin, stdout, stderr = client.exec_command(f'vim-cmd vmsvc/power.{power_state} {vmid}')
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


def app_top():
    vm_formated_info = []
    for hostname,param in get_esxi_hosts().items():
        # VMにSSH接続
        client.connect(
            hostname=param.get('addr'),
            username=param.get('username'),
            password=param.get('password')
        )
        # VM一覧を結合
        vm_list = get_vms_list()
        vm_power = get_vms_power()
        for vm in vm_list:
            vm['uniq_id'] = hostname + '|' + vm.get('id')
            vm['esxi_host'] = hostname
            vm['esxi_addr'] = param.get('addr')
            try:
                vm['power'] = vm_power[vm['id']]
            except KeyError:
                vm['power'] = 'error'
        vm_formated_info.extend(vm_list)
    
    return vm_formated_info


def app_detail(uniq_id):
    hostname,vmid = uniq_id.split('|')
    return get_vm_detail(hostname, vmid)


def app_set_power(uniq_id, power_state):
    hostname, vmid = uniq_id.split('|')
    return set_vm_power(hostname, vmid, power_state)


def main():
    for a in app_top():
        print(a['uniq_id'])


if __name__ == '__main__':
    main()

