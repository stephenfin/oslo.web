# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from importlib import metadata

from oslo_config import cfg

from oslo_web import opts
from oslo_web.tests import base


class TestOptions(base.BaseTestCase):
    def test_registration_matches_listing(self):
        conf = cfg.ConfigOpts()
        opts.register_wsgi_opts(conf)
        self.assertEqual('api-paste.ini', conf.oslo_web.api_paste_config)
        self.assertEqual('warn', conf.oslo_web.response_validation)
        self.assertEqual(
            {'oslo_web': ['api_paste_config', 'response_validation']},
            {
                group: [opt.name for opt in options]
                for group, options in opts.list_opts()
            },
        )

    def test_config_generator_entry_point(self):
        entry_points = metadata.entry_points(group='oslo.config.opts')
        entry_point = next(ep for ep in entry_points if ep.name == 'oslo.web')
        self.assertIs(opts.list_opts, entry_point.load())
