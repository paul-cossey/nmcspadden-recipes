#!/usr/local/autopkg/python
#
# Copyright (c) Facebook, Inc. and its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""See docstring for AppleURLSearcher class"""

import datetime
import json
import os
import posixpath
import re

from urllib.parse import urlsplit

from autopkglib import ProcessorError, URLGetter


__all__ = ["AppleURLSearcher"]


class AppleURLSearcher(URLGetter):
    """Search the various Apple URLs for a matching Xcode."""

    description = __doc__
    input_variables = {
        "result_output_var_name": {
            "description": (
                "The name of the output variable that is returned "
                "by the match. If not specified then a default of "
                "'match' will be used."
            ),
            "default": "match",
            "required": False
        },
        "re_pattern": {
            "description": (
                "Path to download data file from AppleCookieDownloader."
                "Ignored if BETA is set in the environment."
            ),
            "required": True
        }
    }
    output_variables = {
        "result_output_var_name": {
            "description": (
                "First matched sub-pattern from input found on the fetched "
                "URL. Note the actual name of variable depends on the input "
                "variable 'result_output_var_name' or is assigned a default "
                "of 'match.'"
            )
        }
    }


    def parse_beta_info(self, url):
        """Parse download url to set beta environment variables"""
        self.output(f"Parsing for beta version with url: {url}")
        split_url = url.split("/")
        matched_item = split_url[-1]
        if matched_item.endswith(".xip"):
            matched_item = matched_item.replace(".xip", "")
        split_matched_item = matched_item.split("_")
        if "beta" in split_matched_item:
            self.env["is_beta"] = True
            if split_matched_item[-1].isnumeric():
                self.env["beta_version"] = split_matched_item[-1]
            else:
                # Normalizing beta_version to 1
                self.env["beta_version"] = "1"
            self.output(f"beta_version: {self.env['beta_version']}")
            return
        # Check for Release Candidate versions
        if "Release" in split_matched_item and "Candidate" in split_matched_item:
            self.env["is_beta"] = True
            if split_matched_item[-1].isnumeric():
                self.env["beta_version"] = f"Release Candidate {split_matched_item[-1]}"
            else:
                self.env["beta_version"] = "Release Candidate"
            self.output(f"beta_version: {self.env['beta_version']}")
            return
        self.env["is_beta"] = False
        self.output(f"is_beta: {self.env['is_beta']}")


    # This code is taken directly from URLTextSearcher
    def search_for(self, content, re_pattern, flags=None):
        """Get data from url and search for re_pattern"""
        flag_accumulator = 0
        if flags:
            for flag in flags:
                if flag in re.__dict__:
                    flag_accumulator += re.__dict__[flag]

        re_pattern = re.compile(re_pattern, flags=flag_accumulator)
        # Output this to disk so I can search it later
        with open(
            os.path.join(
                self.env["RECIPE_CACHE_DIR"], "downloads", "url_text.txt"
            ),
            "w",
        ) as f:
            f.write(content)
        if match := re_pattern.search(content):
            # return the last matched group with the dict of named groups
            return match[(match.lastindex or 0)], match.groupdict()
        else:
            raise ProcessorError("No match found in URL")


    def output_result(self, url):
        """Output the desired result."""
        # The final entry is the highest one
        self.output(f"Full URL: {url}")
        self.env[self.env["result_output_var_name"]] = url
        self.output_variables = {self.env["result_output_var_name"]: url}


    def main(self):
        # If we have "URL" already passed in, we should just use it
        if self.env.get("URL"):
            self.output_result(self.env["URL"])
            self.parse_beta_info(self.env["URL"])
            return

        if self.env.get("BETA"):
            self.output("Beta flag is set, searching Apple downloads URL...")
            beta_url = "https://developer.apple.com/download/"
            # We're going to make the strong assumption that if BETA is
            # populated, we should only use URLTextSearcher, because as of
            # 6/7/19, Apple has only posted the new Xcode beta to the main
            # developer page, and not the "More Downloads" section.
            # If this trend holds true, then URLTextSearcher = betas,
            # AppleURLSearcher = "more downloads" = stable/GM releases.
            # If we do get a url from URLTextSearcher, it needs to be appended
            # to the base Apple Developer Portal URL.
            download_dir = os.path.join(self.env["RECIPE_CACHE_DIR"], "downloads")
            login_cookies = os.path.join(download_dir, "login_cookies")
            download_cookies = os.path.join(download_dir, "download_cookies")
            curl_opts = [
                "--url",
                beta_url,
                "--cookie",
                login_cookies,
                "--cookie-jar",
                download_cookies
            ]
            # Initialize the curl_cmd, add base curl options, and execute curl
            prepped_curl_cmd = self.prepare_curl_cmd()
            pattern = r"""<a href=["'](.*.xip)"""
            content = self.download_with_curl(prepped_curl_cmd + curl_opts)
            groupmatch, groupdict = self.search_for(content, pattern)
            fixed_url = f"https://developer.apple.com{groupmatch}"
            self.env[self.env["result_output_var_name"]] = fixed_url
            self.parse_beta_info(fixed_url)
            self.output(f"New fixed URL: {fixed_url}")
            self.output_variables = {
                self.env["result_output_var_name"]: fixed_url
            }
            return

        self.output("Beta flag not set, searching More downloads list...")
        # If we're not looking for BETA, then disregard everything from
        # URLTextSearcher and search the Apple downloads list instead.
        download_dir = os.path.join(self.env["RECIPE_CACHE_DIR"], "downloads")
        downloads = os.path.join(download_dir, "listDownloads.json")
        if not os.path.exists(downloads):
            raise ProcessorError(
                "Missing the download data from AppleCookieDownloader"
            )
        pattern = self.env["re_pattern"]
        with open(downloads) as f:
            data = json.load(f)
        dl_base_url = "https://download.developer.apple.com"
        xcode_list = []
        for x in data["downloads"]:
            for y in x["files"]:
                url = dl_base_url + y["remotePath"]
                # Regex the results
                re_pattern = re.compile(pattern)
                dl_match = re_pattern.findall(url)
                if not dl_match:
                    continue
                filename = os.path.splitext(
                    posixpath.basename(urlsplit(y["remotePath"]).path)
                )[0]
                xcode_item = {
                    "datePublished_str": x["datePublished"],
                    "datePublished_obj": datetime.datetime.strptime(
                        x["datePublished"], "%m/%d/%y %H:%M"
                    ),
                    "remotePath": y["remotePath"],
                    "filename": filename,
                    "full_url": url,
                }
                xcode_list.append(xcode_item)
        matches = sorted(xcode_list, key=lambda i: i["datePublished_obj"])
        match = matches[-1]
        if not match or not xcode_list:
            raise ProcessorError("No match found!")
        self.output(
            f"Sorted list of possible filenames: {[x['filename'] for x in matches]}",
            verbose_level=2
        )
        self.output(f"Found matching item: {match['filename']}")
        if not (full_url_match := match["full_url"]):
            raise ProcessorError("No matching URL found!")
        self.parse_beta_info(full_url_match)
        self.output_result(full_url_match)


if __name__ == "__main__":
    PROCESSOR = AppleURLSearcher()
    PROCESSOR.execute_shell()
