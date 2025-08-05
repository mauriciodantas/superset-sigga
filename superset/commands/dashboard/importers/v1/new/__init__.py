# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
import logging
from functools import partial
from typing import Any, Optional

from marshmallow import Schema
from marshmallow.exceptions import ValidationError
from sqlalchemy.sql import delete, insert

from superset import db
from superset.charts.schemas import ImportV1ChartSchema
from superset.commands.base import BaseCommand
from superset.commands.chart.importers.v1.utils import import_chart_as_new
from superset.commands.dashboard.importers.v1.utils import (
    import_dashboard_as_new,
    update_charts_relationship,
)
from superset.commands.database.importers.v1.utils import get_database_by_uuid_or_fail
from superset.commands.dataset.importers.v1.utils import import_dataset_as_new
from superset.commands.exceptions import CommandInvalidError, ImportFailedError
from superset.commands.importers.v1.utils import (
    load_configs,
    load_metadata,
    validate_metadata_type,
)
from superset.dashboards.schemas import ImportV1DashboardSchema
from superset.datasets.schemas import ImportV1DatasetSchema
from superset.models.dashboard import dashboard_slices
from superset.models.slice import Slice
from superset.queries.saved_queries.schemas import ImportV1SavedQuerySchema
from superset.utils.decorators import on_error, transaction

class ImportDashboardsNewCommand(BaseCommand):
    """
    Command for importing databases, datasets, charts, dashboards and saved queries.

    This command is used for managing Superset assets externally under source control,
    and will overwrite everything.
    """

    schemas: dict[str, Schema] = {
        "charts/": ImportV1ChartSchema(),
        "dashboards/": ImportV1DashboardSchema(),
        "datasets/": ImportV1DatasetSchema(),
        "queries/": ImportV1SavedQuerySchema(),
    }

    # pylint: disable=unused-argument
    def __init__(self, contents: dict[str, str], *args: Any, **kwargs: Any):
        self.contents = contents
        self.passwords: dict[str, str] = kwargs.get("passwords") or {}
        self.ssh_tunnel_passwords: dict[str, str] = (
            kwargs.get("ssh_tunnel_passwords") or {}
        )
        self.ssh_tunnel_private_keys: dict[str, str] = (
            kwargs.get("ssh_tunnel_private_keys") or {}
        )
        self.ssh_tunnel_priv_key_passwords: dict[str, str] = (
            kwargs.get("ssh_tunnel_priv_key_passwords") or {}
        )
        self.database_uuid: str = kwargs.get("database_uuid") or ""
        self.schema: str = kwargs.get("schema") or ""
        self._configs: dict[str, Any] = {}

    # pylint: disable=too-many-locals
    @staticmethod
    def _import_as_new(database_uuid:str, schema:str, configs: dict[str, Any]) -> None:

        # get database
        database = get_database_by_uuid_or_fail(database_uuid)

        def get_default_catalog(self) -> str | None:
            """
            Return the default configured catalog for the database.
            """
            return self.db_engine_spec.get_default_catalog(self)

        # import datasets
        dataset_info: dict[str, dict[str, Any]] = {}
        for file_name, config in configs.items():
            logging.warning(file_name)
            if file_name.startswith("datasets/"):
                config["database_uuid"] = database.uuid
                config["database_id"] = database.id
                config["schema"] = schema
                config["catalog"] = database.get_default_catalog()
                dataset_uuid_origin= config["uuid"]
                dataset = import_dataset_as_new(config, overwrite=False)
                dataset_info[dataset_uuid_origin] = {
                    "datasource_id": dataset.id,
                    "uuid": dataset.uuid,
                    "datasource_type": dataset.datasource_type,
                    "datasource_name": dataset.table_name,
                }

        # import charts
        charts = []
        chart_ids: dict[str, Slice] = {}
        for file_name, config in configs.items():
            if file_name.startswith("charts/"):
                original_uuid_chart = config["uuid"]
                dataset_dict = dataset_info[config["dataset_uuid"]]
                config.update(dataset_dict)
                dataset_uid = f"{dataset_dict['datasource_id']}__{dataset_dict['datasource_type']}"
                config["params"].update({"datasource": dataset_uid})
                if "query_context" in config:
                    config["query_context"] = None
                chart = import_chart_as_new(config)
                charts.append(chart)
                chart_ids[original_uuid_chart] = chart

        # import dashboards
        for file_name, config in configs.items():
            if file_name.startswith("dashboards/"):
                config = update_charts_relationship(config, chart_ids)
                dashboard = import_dashboard_as_new(config)

                # set ref in the dashboard_slices table
                dashboard_chart_ids: list[dict[str, int]] = []
                position = dashboard.position
                for child in position.values():
                    if isinstance(child, dict) and child.get("type") == "CHART":
                        chart_id = child["meta"]["chartId"]
                        dashboard_chart_id = {
                            "dashboard_id": dashboard.id,
                            "slice_id": chart_id,
                        }
                        dashboard_chart_ids.append(dashboard_chart_id)

                db.session.execute(insert(dashboard_slices).values(dashboard_chart_ids))

    @transaction(
        on_error=partial(
            on_error,
            catches=(Exception,),
            reraise=ImportFailedError,
        )
    )
    def run(self) -> None:
        self.validate()
        self._import_as_new(self.database_uuid, self.schema, self._configs)

    def validate(self) -> None:
        exceptions: list[ValidationError] = []

        # verify that the metadata file is present and valid
        try:
            metadata: Optional[dict[str, str]] = load_metadata(self.contents)
        except ValidationError as exc:
            exceptions.append(exc)
            metadata = None

        if not self.database_uuid:
            exceptions.append(ValidationError("Database ID is required"))
        if not self.schema:
            exceptions.append(ValidationError("Schema is required"))

        validate_metadata_type(metadata, "Dashboard", exceptions)

        self._configs = load_configs(
            self.contents,
            self.schemas,
            self.passwords,
            exceptions,
            self.ssh_tunnel_passwords,
            self.ssh_tunnel_private_keys,
            self.ssh_tunnel_priv_key_passwords,
        )

        if exceptions:
            logging.error(exceptions)
            raise CommandInvalidError(
                "Error importing dashboards",
                exceptions,
            )
