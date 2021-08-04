import datetime
import pathlib
import re

import packaging.version
import requests
import tabulate

FILE_HEAD = r"""
.. _plugin-list:

Plugin List
===========

PyPI projects that match "pytest-\*" are considered plugins and are listed
automatically. Packages classified as inactive are excluded.
"""
DEVELOPMENT_STATUS_CLASSIFIERS = (
    "Development Status :: 1 - Planning",
    "Development Status :: 2 - Pre-Alpha",
    "Development Status :: 3 - Alpha",
    "Development Status :: 4 - Beta",
    "Development Status :: 5 - Production/Stable",
    "Development Status :: 6 - Mature",
    "Development Status :: 7 - Inactive",
)


def iter_plugins():
    regex = r">([\d\w-]*)</a>"
    response = requests.get("https://pypi.org/simple")
    for match in re.finditer(regex, response.text):
        name = match.groups()[0]
        if not name.startswith("pytest-"):
            continue
        response = requests.get(f"https://pypi.org/pypi/{name}/json")
        if response.status_code == 404:
            # Some packages, like pytest-azurepipelines42, are included in https://pypi.org/simple but
            # return 404 on the JSON API. Skip.
            continue
        response.raise_for_status()
        info = response.json()["info"]
        if "Development Status :: 7 - Inactive" in info["classifiers"]:
            continue
        for classifier in DEVELOPMENT_STATUS_CLASSIFIERS:
            if classifier in info["classifiers"]:
                status = classifier[22:]
                break
        else:
            status = "N/A"
        requires = "N/A"
        if info["requires_dist"]:
            for requirement in info["requires_dist"]:
                if requirement == "pytest" or "pytest " in requirement:
                    requires = requirement
                    break
        releases = response.json()["releases"]
        for release in sorted(releases, key=packaging.version.parse, reverse=True):
            if releases[release]:
                release_date = datetime.date.fromisoformat(
                    releases[release][-1]["upload_time_iso_8601"].split("T")[0]
                )
                last_release = release_date.strftime("%b %d, %Y")
                break
        name = f'`{info["name"]} <{info["project_url"]}>`_'
        summary = info["summary"].replace("\n", "")
        summary = re.sub(r"_\b", "", summary)
        yield {
            "name": name,
            "summary": summary,
            "last release": last_release,
            "status": status,
            "requires": requires,
        }


def main():
    plugins = list(iter_plugins())
    plugin_table = tabulate.tabulate(plugins, headers="keys", tablefmt="rst")
    plugin_list = pathlib.Path("doc", "en", "reference", "plugin_list.rst")
    with plugin_list.open("w") as f:
        f.write(FILE_HEAD)
        f.write(f"This list contains {len(plugins)} plugins.\n\n")
        f.write(plugin_table)
        f.write("\n")


if __name__ == "__main__":
    main()
