import json
import logging
import shlex

from aws_gate.constants import (
    AWS_DEFAULT_PROFILE,
    AWS_DEFAULT_REGION,
    DEFAULT_OS_USER,
    DEFAULT_SSH_PORT,
    DEFAULT_KEY_ALGORITHM,
    DEFAULT_KEY_SIZE,
    PLUGIN_INSTALL_PATH,
    DEBUG,
    DEFAULT_GATE_DIR,
)
from aws_gate.decorators import (
    plugin_version,
    plugin_required,
    valid_aws_profile,
    valid_aws_region,
)
from aws_gate.query import query_instance
from aws_gate.session_common import BaseSession
from aws_gate.ssh_common import SshKey, SshKeyUploader
from aws_gate.utils import (
    get_aws_client,
    get_aws_resource,
    fetch_instance_details_from_config,
    get_instance_details,
    execute,
)

logger = logging.getLogger(__name__)


class SshSession(BaseSession):
    def __init__(
        self,
        instance_id,
        key_path,
        ssm=None,
        region_name=AWS_DEFAULT_REGION,
        profile_name=AWS_DEFAULT_PROFILE,
        port=DEFAULT_SSH_PORT,
        user=DEFAULT_OS_USER,
        command=None,
        agent_mode=False
    ):
        self._instance_id = instance_id
        self._region_name = region_name
        self._profile_name = profile_name if profile_name is not None else ""
        self._ssm = ssm
        self._port = port
        self._user = user
        self._command = command
        self._key_path = key_path
        self._agent_mode = agent_mode

        self._ssh_cmd = None

        self._session_parameters = {
            "Target": self._instance_id,
            "DocumentName": "AWS-StartSSHSession",
            "Parameters": {"portNumber": [str(self._port)]},
        }

    def _build_ssh_command(self):
        cmd = [
            "ssh",
            "-l",
            self._user,
            "-p",
            str(self._port),
            "-F",
            "/dev/null",
        ]

        if DEBUG:
            cmd.append("-vv")
        else:
            cmd.append("-q")

        proxy_command_args = [
            PLUGIN_INSTALL_PATH,
            json.dumps(self._response),
            self._region_name,
            "StartSession",
            self._profile_name,
            json.dumps(self._session_parameters),
            self._ssm.meta.endpoint_url,
        ]
        proxy_command = " ".join(shlex.quote(i) for i in proxy_command_args)

        ssh_options = [
            "IdentitiesOnly={}".format("no" if self._agent_mode else "yes"),
            "IdentityFile={}".format(self._key_path),
            "UserKnownHostsFile=/dev/null",
            "StrictHostKeyChecking=no",
            "ProxyCommand={}".format(proxy_command),
        ]

        for ssh_option in ssh_options:
            cmd.append("-o")
            cmd.append(ssh_option)

        cmd.append(self._instance_id)

        if self._command:
            cmd.append("--")
            cmd.extend(self._command)

        return cmd

    def open(self):
        self._ssh_cmd = self._build_ssh_command()

        return execute(self._ssh_cmd[0], self._ssh_cmd[1:])


@plugin_required
@plugin_version("1.1.23.0")
@valid_aws_profile
@valid_aws_region
def ssh(
    config,
    instance_name,
    user=DEFAULT_OS_USER,
    port=DEFAULT_SSH_PORT,
    key_type=DEFAULT_KEY_ALGORITHM,
    key_size=DEFAULT_KEY_SIZE,
    profile_name=AWS_DEFAULT_PROFILE,
    region_name=AWS_DEFAULT_REGION,
    command=None,
    agent_mode=False
):
    instance, profile, region = fetch_instance_details_from_config(
        config, instance_name, profile_name, region_name
    )

    ssm = get_aws_client("ssm", region_name=region, profile_name=profile)
    ec2 = get_aws_resource("ec2", region_name=region, profile_name=profile)
    ec2_ic = get_aws_client(
        "ec2-instance-connect", region_name=region, profile_name=profile
    )

    instance_id = query_instance(name=instance, ec2=ec2)
    if instance_id is None:
        raise ValueError("No instance could be found for name: {}".format(instance))

    az = get_instance_details(instance_id=instance_id, ec2=ec2)["availability_zone"]

    logger.info(
        "Opening SSH session on instance %s (%s) via profile %s",
        instance_id,
        region,
        profile,
    )
    key_path = "{}/{}.{}.{}".format(
        DEFAULT_GATE_DIR,
        instance_id,
        region_name,
        profile_name
    )
    with SshKey(key_type=key_type, key_size=key_size, key_path=key_path, agent_mode=agent_mode) as ssh_key:
        with SshKeyUploader(
            instance_id=instance_id, az=az, user=user, ssh_key=ssh_key, ec2_ic=ec2_ic
        ):
            with SshSession(
                instance_id,
                region_name=region,
                profile_name=profile,
                ssm=ssm,
                port=port,
                user=user,
                command=command,
                key_path=key_path,
                agent_mode=agent_mode
            ) as ssh_session:
                ssh_session.open()
