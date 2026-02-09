# Copyright (C) 2018 Red Hat, Inc. Daniel Walsh <dwalsh@redhat.com>

# This file is part of the sos project: https://github.com/sosreport/sos
#
# This copyrighted material is made available to anyone wishing to use,
# modify, copy, or redistribute it subject to the terms and conditions of
# version 2 of the GNU General Public License.
#
# See the LICENSE file in the source distribution for further information.

from sos.report.plugins import Plugin, RedHatPlugin, UbuntuPlugin, PluginOpt


class Podman(Plugin, RedHatPlugin, UbuntuPlugin):
    """Podman is a daemonless container management engine, and this plugin is
    meant to provide diagnostic information for both the engine and the
    containers that podman is managing.

    General status information will be collected from podman commands, while
    detailed inspections of certain components will provide more insight
    into specific container problems. This detailed inspection is provided for
    containers, images, networks, and volumes. Per-entity inspections will be
    recorded in subdirs within sos_commands/podman/ for each of those types.
    """

    short_desc = 'Podman containers'
    plugin_name = 'podman'
    profiles = ('container',)
    packages = ('podman',)

    option_list = [
        PluginOpt('all', default=False,
                  desc='collect for all containers, even terminated ones',
                  long_desc=(
                    'Enable collection for all containers that exist on the '
                    'system regardless of their running state. This may cause '
                    'a significant increase in sos archive size, especially '
                    'when combined with the \'logs\' option.')),
        PluginOpt('logs', default=False,
                  desc='collect stdout/stderr logs for containers',
                  long_desc=(
                    'Capture \'podman logs\' output for discovered containers.'
                    ' This may be useful or not depending on how/if the '
                    'container produces stdout/stderr output. Use cautiously '
                    'when also using the \'all\' option.')),
        PluginOpt('size', default=False,
                  desc='collect image sizes for podman ps'),
        PluginOpt('allusers', default=False,
                  desc='collect for all users, including non root users')
    ]

    def setup(self):
        users = ['root']
        if self.get_option('allusers'):
            non_root_users = self.exec_cmd("lslogins -u --noheadings")
            if non_root_users['status'] == 0:
                # parse the command output to get the user names
                users = [
                    # get 2nd string of line that has user name
                    user.split()[1].strip()
                    # split the user data into lines
                    for user in non_root_users['output'].splitlines()
                    if user.split()[1].strip()
                ]

        self.add_dir_listing([
            '/etc/cni',
            '/etc/containers'
        ], recursive=True)

        subcmds = [
            'info',
            'image trust show',
            'images',
            'images --digests',
            'pod ps',
            'port --all',
            'ps',
            'ps -a',
            'stats --no-stream --all',
            'version',
            'volume ls',
            'system df -v',
        ]

        for user in users:
            if not user:
                continue
            command = "sudo -u " + user
            if user == 'root':
                command = ""
            else:
                cmd = self.exec_cmd(f"{command} podman ps -aq")
                # if command is not successful or no container running in
                # a non root user session not collecting the  data.
                if (cmd['status'] != 0
                       or not cmd['output'].strip()):
                    continue

            self.add_cmd_tags({
                f'{command} podman images': 'podman_list_images',
                f'{command} podman ps': 'podman_list_containers'
            })

            self.add_cmd_output(
                    [f"{command} podman {s}" for s in subcmds],
                    subdir=f'{user}/'
            )

            # separately grab ps -s as this can take a *very* long time
            if self.get_option('size'):
                self.add_cmd_output(
                        f'{command} podman ps -as',
                        priority=100,
                        subdir=f'{user}/'
                )

            pnets = self.collect_cmd_output(
                    f'{command} podman network ls',
                    subdir=f'{user}/networks',
                    tags='podman_list_networks'
            )
            if pnets['status'] == 0:
                nets = [
                    pn.split()[0]
                    for pn in pnets['output'].splitlines()[1:]
                ]
                self.add_cmd_output(
                    [
                        f"{command} podman network inspect {net}"
                        for net in nets
                    ],
                    subdir=f'{user}/networks',
                    tags='podman_network_inspect'
                )

            if user == 'root':
                containers = [
                    c[0] for c in self.get_containers(runtime='podman',
                                          get_all=self.get_option('all'))
                ]
                images = self.get_container_images(runtime=f'podman')
                volumes = self.get_container_volumes(runtime=f'podman')
            else:
                # getting the containers, images and volumes info for non-root
                # user as runtime fetches these info for root user only.
                cmd = f"{command} podman ps"
                if self.get_option('all'):
                    cmd = f"{command} podman ps -a"
                containers_data = self.exec_cmd(cmd)
                containers = []
                if containers_data['status'] == 0:
                    # parse to get container id
                    containers = [
                        # get 1st column container id
                        container.split()[0]
                        # skip the heading line
                        for container in containers_data['output']
                                            .splitlines()[1:]
                        if container.strip()
                    ]

                image_data = self.collect_cmd_output(
                    f'{command} podman images --no-trunc', subdir=f'{user}/'
                )
                images = []
                volumes = []
                if image_data['status'] == 0:
                    # parse to get the image data{name:tag,id}
                    images = [
                        (f"{image[0]}:{image[1]}", image[2])
                        for image in (
                            #split the each line into columns
                            img.split()
                            # split into lines and skip the heading line
                            for img in image_data['output']
                                                .strip()
                                                .split("\n")[1:]
                        )
                    ]
                vols = self.exec_cmd(
                        f'{command} podman volume ls --format "{{{{.Name}}}}"'
                )
                if vols['status'] == 0:
                    volumes = [
                        # parse to get the volume names
                        v for v in vols['output'].splitlines() if v.strip()
                    ]

            for container in containers:
                self.add_cmd_output(f"{command} podman inspect {container}",
                                    subdir=f'{user}/containers',
                                    tags='podman_container_inspect')

            for img in images:
                name, img_id = img
                insp = name if 'none' not in name else img_id
                self.add_cmd_output(
                    f"{command} podman inspect {insp}",
                    subdir=f'{user}/images',
                    tags='podman_image_inspect'
                )
                self.add_cmd_output(
                    f"{command} podman image tree {insp}",
                    subdir=f'{user}/images/tree',
                    tags='podman_image_tree'
                )

            for vol in volumes:
                self.add_cmd_output(f"{command} podman volume inspect {vol}",
                                    subdir=f'{user}/volumes',
                                    tags='podman_volume_inspect')

            if self.get_option('logs'):
                for con in containers:
                    self.add_cmd_output(f"{command} podman logs -t {con}",
                                    subdir=f'{user}/containers', priority=50)

    def postproc(self):
        # Attempts to match key=value pairs inside container inspect output
        # for potentially sensitive items like env vars that contain passwords.
        # Typically, these will be seen in env elements or similar, and look
        # like this:
        #             "Env": [
        #                "mypassword=supersecret",
        #                "container=oci"
        #             ],
        # This will mask values when the variable name looks like it may be
        # something worth obfuscating.

        env_regexp = r'(?P<var>(pass|key|secret|PASS|KEY|SECRET).*?)=' \
                      '(?P<value>.*?)"'
        self.do_cmd_output_sub('*inspect*', env_regexp,
                               r'\g<var>=********"')

# vim: set et ts=4 sw=4 :
