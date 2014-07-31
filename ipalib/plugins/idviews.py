# Authors:
#   Alexander Bokovoy <abokovoy@redhat.com>
#   Tomas Babej <tbabej@redhat.com>
#
# Copyright (C) 2014  Red Hat
# see file 'COPYING' for use and warranty information
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from ipalib.plugins.baseldap import (LDAPQuery, LDAPObject, LDAPCreate,
                                     LDAPDelete, LDAPUpdate, LDAPSearch,
                                     LDAPRetrieve, global_output_params)
from ipalib.plugins.hostgroup import get_complete_hostgroup_member_list
from ipalib import api, Str, Int, _, ngettext, errors, output
from ipalib.plugable import Registry

from ipapython.dn import DN

__doc__ = _("""
ID views
Manage ID views
IPA allows to override certain properties of users and groups per each host.
This functionality is primarily used to allow migration from older systems or
other Identity Management solutions.
""")

register = Registry()


@register()
class idview(LDAPObject):
    """
    ID view object.
    """

    container_dn = api.env.container_views
    object_name = _('ID view')
    object_name_plural = _('ID views')
    object_class = ['ipaIDView', 'top']
    default_attributes = ['cn', 'description']
    rdn_is_primary_key = True

    label = _('ID views')
    label_singular = _('ID view')

    takes_params = (
        Str('cn',
            cli_name='name',
            label=_('ID View Name'),
            primary_key=True,
        ),
        Str('description?',
            cli_name='desc',
            label=_('Description'),
        ),
    )

    permission_filter_objectclasses = ['nsContainer']
    managed_permissions = {
        'System: Read ID Views': {
            'ipapermbindruletype': 'all',
            'ipapermright': {'read', 'search', 'compare'},
            'ipapermdefaultattr': {
                'cn', 'description', 'objectClass',
            },
        },
    }


@register()
class idview_add(LDAPCreate):
    __doc__ = _('Add a new ID View.')
    msg_summary = _('Added ID view "%(value)s"')


@register()
class idview_del(LDAPDelete):
    __doc__ = _('Delete an ID view.')
    msg_summary = _('Deleted ID view "%(value)s"')


@register()
class idview_mod(LDAPUpdate):
    __doc__ = _('Modify an ID view.')
    msg_summary = _('Modified an ID view "%(value)s"')


@register()
class idview_find(LDAPSearch):
    __doc__ = _('Search for an ID view.')
    msg_summary = ngettext('%(count)d ID view matched',
                           '%(count)d ID views matched', 0)


@register()
class idview_show(LDAPRetrieve):
    __doc__ = _('Display information about an ID view.')


@register()
class idview_apply(LDAPQuery):
    __doc__ = _('Applies ID view to specified hosts or current members of '
                'specified hostgroups. If any other ID view is applied to '
                'the host, it is overriden.')

    member_count_out = (_('ID view applied to %i host.'),
                        _('ID view applied to %i hosts.'))

    msg_summary = 'Applied ID view "%(value)s"'

    takes_options = (
        Str('host*',
            cli_name='hosts',
            doc=_('Hosts to apply the ID view to'),
            label=_('hosts'),
        ),
        Str('hostgroup*',
            cli_name='hostgroups',
            doc=_('Hostgroups to whose hosts apply the ID view to. Please note '
                  'that view is not applied automatically to any hosts added '
                  'to the hostgroup after running the idview-apply command.'),
            label=_('hostgroups'),
        ),
    )

    has_output = (
        output.summary,
        output.Output('succeeded',
            type=dict,
            doc=_('Hosts that this ID view was applied to.'),
        ),
        output.Output('failed',
            type=dict,
            doc=_('Hosts or hostgroups that this ID view could not be '
                  'applied to.'),
        ),
        output.Output('completed',
            type=int,
            doc=_('Number of hosts the ID view was applied to:'),
        ),
    )

    has_output_params = global_output_params

    def execute(self, *keys, **options):
        view = keys[-1] if keys else None
        ldap = self.obj.backend

        # Test if idview actually exists, if it does not, NotFound is raised
        if not options.get('clear_view', False):
            view_dn = self.api.Object['idview'].get_dn_if_exists(view)
            assert isinstance(view_dn, DN)
        else:
            # In case we are removing assigned view, we modify the host setting
            # the ipaAssignedIDView to None
            view_dn = None

        completed = 0
        succeeded = {'host': []}
        failed = {
            'host': [],
            'hostgroup': [],
            }

        # Generate a list of all hosts to apply the view to
        hosts_to_apply = list(options.get('host', []))

        for hostgroup in options.get('hostgroup', ()):
            try:
                hosts_to_apply += get_complete_hostgroup_member_list(hostgroup)
            except errors.NotFound:
                failed['hostgroup'].append((hostgroup, "not found"))
            except errors.PublicError as e:
                failed['hostgroup'].append((hostgroup, "%s : %s" % (
                                            e.__class__.__name__, str(e))))

        for host in hosts_to_apply:
            try:
                host_dn = api.Object['host'].get_dn_if_exists(host)

                host_entry = ldap.get_entry(host_dn,
                                            attrs_list=['ipaassignedidview'])
                host_entry['ipaassignedidview'] = view_dn

                ldap.update_entry(host_entry)

                # If no exception was raised, view assigment went well
                completed = completed + 1
                succeeded['host'].append(host)
            except errors.EmptyModlist:
                # If view was already applied, do not complain
                pass
            except errors.NotFound:
                failed['host'].append((host, "not found"))
            except errors.PublicError as e:
                failed['host'].append((host, str(e)))

        # Wrap dictionary containing failures in another dictionary under key
        # 'memberhost', since that is output parameter in global_output_params
        # and thus we get nice output in the CLI
        failed = {'memberhost': failed}

        # Sort the list of affected hosts
        succeeded['host'].sort()

        # Note that we're returning the list of affected hosts even if they
        # were passed via referencing a hostgroup. This is desired, since we
        # want to stress the fact that view is applied on all the current
        # member hosts of the hostgroup and not tied with the hostgroup itself.

        return dict(
            summary=unicode(_(self.msg_summary % {'value': view})),
            succeeded=succeeded,
            completed=completed,
            failed=failed,
        )


@register()
class idview_unapply(idview_apply):
    __doc__ = _('Clears ID view from specified hosts or current members of '
                'specified hostgroups.')

    member_count_out = (_('ID view cleared from %i host.'),
                        _('ID view cleared from %i hosts.'))

    msg_summary = 'Cleared ID views'

    takes_options = (
        Str('host*',
            cli_name='hosts',
            doc=_('Hosts to clear (any) ID view from.'),
            label=_('hosts'),
        ),
        Str('hostgroup*',
            cli_name='hostgroups',
            doc=_('Hostgroups whose hosts should have ID views cleared. Note '
                  'that view is not cleared automatically from any host added '
                  'to the hostgroup after running idview-unapply command.'),
            label=_('hostgroups'),
        ),
    )

    has_output = (
        output.summary,
        output.Output('succeeded',
            type=dict,
            doc=_('Hosts that ID view was cleared from.'),
        ),
        output.Output('failed',
            type=dict,
            doc=_('Hosts or hostgroups that ID view could not be cleared '
                  'from.'),
        ),
        output.Output('completed',
            type=int,
            doc=_('Number of hosts that had a ID view was unset:'),
        ),
    )

    # Take no arguments, since ID View reference is not needed to clear
    # the hosts
    def get_args(self):
        return ()

    def execute(self, *keys, **options):
        options['clear_view'] = True
        return super(idview_unapply, self).execute(*keys, **options)


@register()
class idoverride(LDAPObject):
    """
    ID override object.
    """

    parent_object = 'idview'
    container_dn = api.env.container_views

    object_name = _('ID override')
    object_name_plural = _('ID overrides')
    object_class = ['ipaOverrideAnchor', 'top']
    default_attributes = [
        'cn', 'description', 'ipaAnchorUUID', 'gidNumber',
        'homeDirectory', 'uidNumber', 'uid',
    ]

    label = _('ID overrides')
    label_singular = _('ID override')
    rdn_is_primary_key = True

    takes_params = (
        Str('ipaanchoruuid',
            cli_name='anchor',
            primary_key=True,
            label=_('Anchor to override'),
        ),
        Str('description',
            cli_name='desc',
            label=_('Description'),
        ),
        Str('cn?',
            pattern='^[a-zA-Z0-9_.][a-zA-Z0-9_.-]{0,252}[a-zA-Z0-9_.$-]?$',
            pattern_errmsg='may only include letters, numbers, _, -, . and $',
            maxlength=255,
            cli_name='group_name',
            label=_('Group name'),
            normalizer=lambda value: value.lower(),
        ),
        Int('gidnumber?',
            cli_name='gid',
            label=_('GID'),
            doc=_('Group ID Number'),
            minvalue=1,
        ),
        Str('uid?',
            pattern='^[a-zA-Z0-9_.][a-zA-Z0-9_.-]{0,252}[a-zA-Z0-9_.$-]?$',
            pattern_errmsg='may only include letters, numbers, _, -, . and $',
            maxlength=255,
            cli_name='login',
            label=_('User login'),
            normalizer=lambda value: value.lower(),
        ),
        Int('uidnumber?',
            cli_name='uid',
            label=_('UID'),
            doc=_('User ID Number'),
            minvalue=1,
        ),
        Str('homedirectory?',
            cli_name='homedir',
            label=_('Home directory'),
        ),
    )

    permission_filter_objectclasses = ['ipaOverrideAnchor']
    managed_permissions = {
        'System: Read ID Overrides': {
            'ipapermbindruletype': 'all',
            'ipapermright': {'read', 'search', 'compare'},
            'ipapermdefaultattr': {
                'cn', 'objectClass', 'ipaAnchorUUID', 'uidNumber', 'gidNumber',
                'description', 'homeDirectory', 'uid',
            },
        },
    }


@register()
class idoverride_add(LDAPCreate):
    __doc__ = _('Add a new ID override.')
    msg_summary = _('Added ID override "%(value)s"')


@register()
class idoverride_del(LDAPDelete):
    __doc__ = _('Delete an ID override.')
    msg_summary = _('Deleted ID override "%(value)s"')


@register()
class idoverride_mod(LDAPUpdate):
    __doc__ = _('Modify an ID override.')
    msg_summary = _('Modified an ID override "%(value)s"')


@register()
class idoverride_find(LDAPSearch):
    __doc__ = _('Search for an ID override.')
    msg_summary = ngettext('%(count)d ID override matched',
                           '%(count)d ID overrides matched', 0)


@register()
class idoverride_show(LDAPRetrieve):
    __doc__ = _('Display information about an ID override.')