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
import uuid
from typing import Any

from superset import db, security_manager
from superset.commands.exceptions import ImportFailedError
from superset.models.dashboard import Dashboard
from superset.models.slice import Slice
from superset.utils import json
from superset.utils.core import get_user

logger = logging.getLogger(__name__)


JSON_KEYS = {"position": "position_json", "metadata": "json_metadata"}


def find_chart_uuids(position: dict[str, Any]) -> set[str]:
    return set(build_uuid_to_id_map(position))


def find_native_filter_datasets(metadata: dict[str, Any]) -> set[str]:
    uuids: set[str] = set()
    for native_filter in metadata.get("native_filter_configuration", []):
        targets = native_filter.get("targets", [])
        for target in targets:
            dataset_uuid = target.get("datasetUuid")
            if dataset_uuid:
                uuids.add(dataset_uuid)
    return uuids


def build_uuid_to_id_map(position: dict[str, Any]) -> dict[str, int]:
    return {
        child["meta"]["uuid"]: child["meta"]["chartId"]
        for child in position.values()
        if (
            isinstance(child, dict)
            and child["type"] == "CHART"
            and "uuid" in child["meta"]
        )
    }


def update_id_refs(  # pylint: disable=too-many-locals
    config: dict[str, Any],
    chart_ids: dict[str, int],
    dataset_info: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Update dashboard metadata to use new IDs"""
    fixed = config.copy()

    # build map old_id => new_id
    old_ids = build_uuid_to_id_map(fixed["position"])
    id_map = {
        old_id: chart_ids[uuid] for uuid, old_id in old_ids.items() if uuid in chart_ids
    }

    # fix metadata
    metadata = fixed.get("metadata", {})
    if "timed_refresh_immune_slices" in metadata:
        metadata["timed_refresh_immune_slices"] = [
            id_map[old_id] for old_id in metadata["timed_refresh_immune_slices"]
        ]

    if "filter_scopes" in metadata:
        # in filter_scopes the key is the chart ID as a string; we need to update
        # them to be the new ID as a string:
        metadata["filter_scopes"] = {
            str(id_map[int(old_id)]): columns
            for old_id, columns in metadata["filter_scopes"].items()
            if int(old_id) in id_map
        }

        # now update columns to use new IDs:
        for columns in metadata["filter_scopes"].values():
            for attributes in columns.values():
                attributes["immune"] = [
                    id_map[old_id]
                    for old_id in attributes["immune"]
                    if old_id in id_map
                ]

    if "expanded_slices" in metadata:
        metadata["expanded_slices"] = {
            str(id_map[int(old_id)]): value
            for old_id, value in metadata["expanded_slices"].items()
        }

    if "default_filters" in metadata:
        default_filters = json.loads(metadata["default_filters"])
        metadata["default_filters"] = json.dumps(
            {
                str(id_map[int(old_id)]): value
                for old_id, value in default_filters.items()
                if int(old_id) in id_map
            }
        )

    # fix position
    position = fixed.get("position", {})
    for child in position.values():
        if (
            isinstance(child, dict)
            and child["type"] == "CHART"
            and "uuid" in child["meta"]
            and child["meta"]["uuid"] in chart_ids
        ):
            child["meta"]["chartId"] = chart_ids[child["meta"]["uuid"]]

    # fix native filter references
    native_filter_configuration = fixed.get("metadata", {}).get(
        "native_filter_configuration", []
    )
    for native_filter in native_filter_configuration:
        targets = native_filter.get("targets", [])
        for target in targets:
            dataset_uuid = target.pop("datasetUuid", None)
            if dataset_uuid:
                target["datasetId"] = dataset_info[dataset_uuid]["datasource_id"]

        scope_excluded = native_filter.get("scope", {}).get("excluded", [])
        if scope_excluded:
            native_filter["scope"]["excluded"] = [
                id_map[old_id] for old_id in scope_excluded if old_id in id_map
            ]

    return fixed

def import_dashboard(
    config: dict[str, Any],
    overwrite: bool = False,
    ignore_permissions: bool = False,
) -> Dashboard:
    can_write = ignore_permissions or security_manager.can_access(
        "can_write",
        "Dashboard",
    )
    existing = db.session.query(Dashboard).filter_by(uuid=config["uuid"]).first()
    if existing:
        if overwrite and can_write and get_user():
            if not security_manager.can_access_dashboard(existing):
                raise ImportFailedError(
                    "A dashboard already exists and user doesn't "
                    "have permissions to overwrite it"
                )
        elif not overwrite or not can_write:
            return existing
        config["id"] = existing.id
    elif not can_write:
        raise ImportFailedError(
            "Dashboard doesn't exist and user doesn't "
            "have permission to create dashboards"
        )

    # TODO (betodealmeida): move this logic to import_from_dict
    config = config.copy()

    # removed in https://github.com/apache/superset/pull/23228
    if "metadata" in config and "show_native_filters" in config["metadata"]:
        del config["metadata"]["show_native_filters"]

    for key, new_name in JSON_KEYS.items():
        if config.get(key) is not None:
            value = config.pop(key)
            try:
                config[new_name] = json.dumps(value)
            except TypeError:
                logger.info("Unable to encode `%s` field: %s", key, value)

    dashboard = Dashboard.import_from_dict(config, recursive=False)
    if dashboard.id is None:
        db.session.flush()

    if (user := get_user()) and user not in dashboard.owners:
        dashboard.owners.append(user)

    return dashboard

def import_dashboard_as_new(
    config: dict[str, Any],
) -> Dashboard:
    base_name = config["dashboard_title"]
    dashboard_title = base_name
    copy_counter = 1

    while db.session.query(Dashboard).filter_by(
        dashboard_title=dashboard_title
    ).first() is not None:
        dashboard_title = f"Copy {copy_counter} of {base_name}"
        copy_counter += 1

    config["dashboard_title"] = dashboard_title

    config = config.copy()
    config["uuid"] = str(uuid.uuid4())

    # removed in https://github.com/apache/superset/pull/23228
    if "metadata" in config and "show_native_filters" in config["metadata"]:
        del config["metadata"]["show_native_filters"]

    for key, new_name in JSON_KEYS.items():
        if config.get(key) is not None:
            value = config.pop(key)
            try:
                config[new_name] = json.dumps(value)
            except TypeError:
                logger.info("Unable to encode `%s` field: %s", key, value)

    dashboard = Dashboard.import_from_dict(config, recursive=False)
    if dashboard.id is None:
        db.session.flush()

    if (user := get_user()) and user not in dashboard.owners:
        dashboard.owners.append(user)

    return dashboard



def update_charts_relationship(  # pylint: disable=too-many-locals
    config: dict[str, Any],
    chart_info: dict[str, Slice],
) -> dict[str, Any]:
    """Update dashboard metadata to use new chart IDs and UUIDs"""
    fixed = config.copy()

    # Map from old chart ID (as string) to new chart ID (as int)
    old_chart_id_to_new_id: dict[str, int] = {}

    # Update chart references in the dashboard layout ("position")
    position = fixed.get("position", {})
    for child in position.values():
        if isinstance(child, dict) and child.get("type") == "CHART":
            old_chart_uuid = child["meta"]["uuid"]
            old_chart_id = child["meta"]["chartId"]

            new_chart_metadata = chart_info[old_chart_uuid]
            print(f"Updating chart ID {old_chart_id} to {new_chart_metadata}")
            new_chart_uuid = new_chart_metadata.uuid
            new_chart_id = new_chart_metadata.id

            # Save mapping from old ID to new ID
            old_chart_id_to_new_id[str(old_chart_id)] = new_chart_id

            # Update chart UUID and ID in layout
            child["meta"]["uuid"] = new_chart_uuid
            child["meta"]["chartId"] = new_chart_id

    # Update chart_configuration metadata
    metadata = fixed.get("metadata", {})
    chart_configuration = metadata.get("chart_configuration", {})

    new_chart_configuration = {}

    for old_id_str, config_entry in chart_configuration.items():
        new_id = old_chart_id_to_new_id.get(old_id_str)

        if new_id is None:
            continue  # Skip charts without a corresponding new ID

        # Clone and update chart metadata
        updated_entry = config_entry.copy()
        updated_entry["id"] = new_id

        # Update chartsInScope with new IDs
        charts_in_scope = updated_entry.get("crossFilters", {}).get("chartsInScope", [])
        updated_charts_in_scope = [
            old_chart_id_to_new_id.get(str(chart_id), chart_id)
            for chart_id in charts_in_scope
        ]
        logger.warning("Updated chart IDs: %s", updated_charts_in_scope)
        updated_entry["crossFilters"]["chartsInScope"] = updated_charts_in_scope

        # Add the updated entry using the new chart ID as the key
        new_chart_configuration[str(new_id)] = updated_entry

    # Replace the chart_configuration with the updated version
    metadata["chart_configuration"] = new_chart_configuration

    # Update global_chart_configuration.chartsInScope
    global_chart_config = metadata.get("global_chart_configuration", {})
    global_charts_in_scope = global_chart_config.get("chartsInScope", [])

    updated_global_charts_in_scope = [
        old_chart_id_to_new_id.get(str(chart_id), chart_id)
        for chart_id in global_charts_in_scope
    ]
    global_chart_config["chartsInScope"] = updated_global_charts_in_scope
    metadata["global_chart_configuration"] = global_chart_config

    fixed["metadata"] = metadata

    return fixed

