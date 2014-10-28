#! /usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2014 Spotify AB
from __future__ import absolute_import, division, print_function

try:
    from collections import OrderedDict
except ImportError:  # pragma: no cover
    # python 2.6
    from ordereddict import OrderedDict

try:
    import BaseHTTPServer as httpserver
except ImportError:
    import http.server as httpserver
import json
import re

import markdown2 as markdown

from .loader import RAMLLoader
from .parameters import (ContentType, FormParameter, URIParameter,
                         QueryParameter, Header, Response, ResourceType,
                         Documentation, SecuritySchemes, Body)


HTTP_RESP_CODES = httpserver.BaseHTTPRequestHandler.responses.keys()


class RAMLParserError(Exception):
    pass


class APIRoot(object):
    def __init__(self, raml_file):
        self.raml = RAMLLoader(raml_file).raml

    @property
    def resources(self):
        """
        Returns a dictionary of RAML resources/endpoints
        """
        resource_stack = ResourceStack(self, self.raml).yield_resources()
        resource = OrderedDict()
        for res in resource_stack:
            key_name = res.method + "-" + res.display_name
            resource[key_name] = res
        return resource

    @property
    def title(self):
        """Title of API"""
        return self.raml.get('title')

    @property
    def version(self):
        """API version"""
        return self.raml.get('version')

    @property
    def protocols(self):
        """Supported protocols"""
        return self.raml.get('protocols')

    @property
    def base_uri(self):
        """Base URI of API"""
        base_uri = self.raml.get('baseUri')
        if base_uri:
            if "{version}" in base_uri:
                try:
                    return base_uri.replace("{version}", self.raml['version'])
                except KeyError:
                    raise RAMLParserError("No API Version defined even though "
                                          "version is referred in the baseUri")
            else:
                return base_uri
        return None

    @property
    def uri_parameters(self):
        """URI Parameters"""
        uri_params = self.raml.get('uriParameters')
        if uri_params:
            params = []
            for k, v in list(uri_params.items()):
                if k == 'version':
                    raise RAMLParserError("'version' can only be defined "
                                          "in baseUriParameters")
                params.append((URIParameter(k, v)))
            return params
        return None

    @property
    def base_uri_parameters(self):
        """URI Parameters for base_uri"""
        base_uri_params = self.raml.get('baseUriParameters')
        if base_uri_params:
            uri_params = []
            for k, v in list(base_uri_params.items()):
                uri_params.append((URIParameter(k, v)))
            return uri_params
        return None

    @property
    def media_type(self):
        """Supported Media Types"""
        return self.raml.get('mediaType')

    @property
    def resource_types(self):
        """Defined Resource Types"""
        resource_types = self.raml.get('resourceTypes')
        if resource_types:
            resources = []
            for resource in resource_types:
                resources.append(ResourceType(list(resource.keys())[0],
                                              list(resource.values())[0]))
            return resources
        return None

    @property
    def documentation(self):
        """User Documentation"""
        documentation = self.raml.get('documentation')
        if documentation:
            if not isinstance(documentation, list):
                msg = "Error parsing documentation"
                raise RAMLParserError(msg)
            docs = []
            for doc in documentation:
                doc = Documentation(doc.get('title'), doc.get('content'))
                docs.append(doc)
            return docs
        return None

    @property
    def security_schemes(self):
        """Supported Security Schemes"""
        return SecuritySchemes(self.raml).security_schemes

    @property
    def traits(self):
        """Defined traits"""
        traits = self.raml.get('traits')
        trait_params = []
        for trait in traits:
            for key, value in list(trait.items()):
                items = value.get('queryParameters')
                for k, v in list(items.items()):
                    trait_params.append({key: QueryParameter(k, v)})
        return trait_params

    def __find_params(self, string):
        # TODO: ignoring humanizers for now
        match = re.findall(r"(<<.*?>>)", string)
        match = [m[2:-2] for m in match]  # clean <<>> first
        ret = []
        for m in match:
            if "!singularlize" or "!pluralize" in m:  # clean out humanizers
                param = m.split(" | ")[0]
                param = "<<{0}>>".format(param)  # then put back <<>>
                if param not in ret:
                    ret.append(param)
            else:
                if m not in ret:
                    param = "<<{0}>>".format(m)
                    ret.append(param)

        return ret

    def __parse_parameters(self):
        """If traits or resourceTypes contain <<parameter>> in definition"""
        _resources_params = []
        if self.resource_types:
            for r in self.resource_types:
                data = json.dumps(r.data)
                match = self.__find_params(data)
                _resources_params += match

        _traits_params = []
        if self.traits:
            for t in self.traits:
                data = json.dumps(list(t.keys()))
                match = self.__find_params(data)
                _traits_params += match

                data = json.dumps(list(t.values())[0].data)
                match = self.__find_params(data)
                _traits_params += match

        return dict(resource_types=list(set(_resources_params)),
                    traits=list(set(_traits_params)))

    def get_parameters(self):
        """Parameters for traits and/or resource_types"""
        return self.__parse_parameters()

    @property
    def schemas(self):
        """User-defined schema with XML, JSON, YAML, etc"""
        return self.raml.get('schemas')


class ResourceStack(object):
    def __init__(self, api, raml_file):
        self.api = api
        self.raml = raml_file

    def yield_resources(self):
        """Yields Resource objects for the API defined in the ramlfile"""
        available_methods = ['get', 'post', 'put', 'delete',
                             'patch', 'head', 'options']
        resource_stack = []

        for k, v in list(self.raml.items()):
            if k.startswith("/"):
                for method in available_methods:
                    if method in self.raml[k].keys():
                        node = Resource(name=k, data=v, method=method,
                                        api=self.api)
                        resource_stack.append(node)
        while resource_stack:
            current = resource_stack.pop(0)
            yield current
            if current.data:
                for child_k, child_v in list(current.data.items()):
                    if child_k.startswith("/"):
                        for method in available_methods:
                            if method in current.data[child_k].keys():
                                child = Resource(name=child_k, data=child_v,
                                                 method=method, parent=current,
                                                 api=self.api)
                                resource_stack.append(child)


class Resource(object):
    def __init__(self, name, data, method, api, parent=None):
        self.name = name
        self.data = data
        self.api = api
        self.parent = parent
        self.method = method

    def _get_path_to(self, node):
        parent_path = ''
        if node.parent:
            parent_path = self._get_path_to(node.parent)
        return parent_path + node.name

    @property
    def display_name(self):
        """
        Returns either the defined displayName for the Resource, or its
        name if none is defined.
        """
        display_name = self.data.get('displayName')
        if not display_name:
            display_name = self.name
        return display_name

    @property
    def description_raw(self):
        """Returns raw (Markdown) text of Resource description"""
        return self.data.get(self.method).get('description')

    @property
    def description_html(self):
        """Returns HTML output of Resource description"""
        return markdown.markdown(self.description_raw)

    @property
    def headers(self):
        _headers = self.data.get(self.method).get('headers')
        headers = []
        if _headers:
            for k, v in list(_headers.items()):
                headers.append(Header(k, v, self.method))
        return headers

    @property
    def path(self):
        """Returns string URI path of Resource"""
        return self._get_path_to(self)

    @property
    def absolute_path(self):
        """Return the full API URL for Resource"""
        return self.api.base_uri + self.path

    def _get_secured_by(self):
        # TODO: put a validator in to check if schemes here are not in
        # list of secured schemes
        if self.data.get('securedBy'):
            secured_by = self.data.get('securedBy')
        elif self.data.get(self.method).get('securedBy'):
            secured_by = self.data.get(self.method).get('securedBy')
        else:
            return None

        _secured_by = []
        for secured in secured_by:
            if isinstance(secured, dict):
                scheme = list(secured.keys())[0]
                if 'scopes' in list(secured.values())[0]:
                    scopes = list(secured.values())[0].get('scopes')
            else:
                scheme = secured
                scopes = None

            _scheme = {}
            for s in self.api.security_schemes:
                if s.name == scheme:
                    _scheme['name'] = s.name
                    _scheme['type'] = s.type
                    _scheme['scheme'] = s
                if scopes:
                    _scheme['scopes'] = scopes

                if _scheme not in _secured_by:
                    _secured_by.append(_scheme)
        return _secured_by

    @property
    def secured_by(self):
        """
        Returns authentication protocol information if Resource is secured
        """
        return self._get_secured_by()

    def __find_params(self, string):
        # TODO: ignoring humanizers for now
        match = re.findall(r"(<<.*?>>)", string)
        match = [m[2:-2] for m in match]  # clean <<>> first
        ret = []
        for m in match:
            if "!singularlize" or "!pluralize" in m:  # clean out humanizers
                param = m.split(" | ")[0]
                param = "<<{0}>>".format(param)  # then put back <<>>
                if param not in ret:
                    ret.append(param)
            else:
                if m not in ret:
                    param = "<<{0}>>".format(m)
                    ret.append(param)

        return ret

    def _fill_reserved_params(self, string):
        if "<<resourcePathName>>" in string:
            if self.name.startswith("/"):
                name = self.name[1:]
            else:
                name = self.name
            string = string.replace("<<resourcePathName>>", name)
        if "<<resourcePath>>" in string:
            string = string.replace("<<resourcePath>>", self.name)

        return string

    def _fill_params(self, string, key, value):
        if key in string:
            string = string.replace("<<" + key + ">>", value)
        string = self._fill_reserved_params(string)
        return string

    def _map_resource_string(self, res_type):
        results = []
        api_resources = self.api.resource_types
        for r in api_resources:
            result = {}
            if r.name == res_type:
                result['name'] = r.name
                result['usage'] = r.usage
                methods = r.methods
                for m in methods:
                    if self.method == m.name:
                        if m.data.get('description'):
                            # If the method has a description attached,
                            # then use it
                            desc = m.data.get('description')
                        else:
                            # otherwise use the general description
                            desc = r.description_raw
                    else:
                        # otherwise use the general description
                        desc = r.description_raw
                    result['description'] = self._fill_reserved_params(desc)
                results.append(result)

        # Would this ever get hit?
        if len(results) > 1:
            msg = "Too many resource types applied to one resource."
            raise RAMLParserError(msg)
        return results[0]

    def _map_resource_dict(self, res_type):
        api_resources = self.api.resource_types

        _type = list(res_type.keys())[0]
        api_resources_names = [a.name for a in api_resources]
        if _type not in api_resources_names:
            msg = "'{0}' is not defined in API Root's resourceTypes."
            raise RAMLParserError(msg)

        for r in api_resources:
            if r.name == _type:
                _values = list(res_type.values())[0]
                data = json.dumps(r.data)
                for k, v in list(_values.items()):
                    data = self._fill_params(data, k, v)
                data = json.loads(data)
                result = dict(name=r.name, data=data)
                return result

    def _get_resource_type(self):
        # NOTE: Extremely naive implementation, esp for dicts
        res_type = self.data.get('type')
        if res_type:
            mapped_res_type = {}
            if isinstance(res_type, str):
                mapped_res_type = self._map_resource_string(res_type)

            elif isinstance(res_type, dict):
                if len(res_type.keys()) > 1:
                    msg = "Too many resource types applied to one resource."
                    raise RAMLParserError(msg)
                mapped_res_type = self._map_resource_dict(res_type)

            else:
                msg = "Error applying resource type '{0}'' to '{1}'.".format(
                    res_type, self.name)
                raise RAMLParserError(msg)
            return mapped_res_type

    @property
    def resource_type(self):
        """Returns a list of resource types assigned to the resource"""
        return self._get_resource_type()

    @property
    def traits(self):
        """Returns a list of traits assigned to the resource"""
        endpoint_traits = self.data.get('is', [])
        method_traits = self.data.get(self.method).get('is', [])
        return endpoint_traits + method_traits

    @property
    def scopes(self):
        """Returns a list of OAuth2 scopes assigned to the resource"""
        if self.secured_by:
            for item in self.secured_by:
                if 'oauth_2_0' in item.values():
                    if self.data.get('securedBy'):
                        for i in self.data.get('securedBy'):
                            if isinstance(i, dict) and 'oauth_2_0' in i.keys():
                                return i.get('oauth_2_0').get('scopes')
                    elif self.data.get(self.method).get('securedBy'):
                        for i in self.data.get('securedBy'):
                            if isinstance(i, dict) and 'oauth_2_0' in i.keys():
                                return i.get('oauth_2_0').get('scopes')
        else:
            return None

    @property
    def protocols(self):
        """Returns a list of supported protocols for the particular resource"""
        return self.data.get(self.method).get('protocols', [])

    def _get_responses(self, node):
        resps = []
        responses = self.data.get(self.method).get('responses')
        if responses:
            for k, v in list(responses.items()):
                if k not in HTTP_RESP_CODES:
                    msg = "{0} not a supported HTTP Response code".format(k)
                    raise RAMLParserError(msg)
                else:
                    resps.append(Response(k, v, self.method))

        return resps

    @property
    def responses(self):
        """Returns a list of Response objects of a resource"""
        return self._get_responses(self)

    def _get_body(self, node):
        bodies = []
        _bodies = self.data.get(self.method).get('body')
        if _bodies:
            for k, v in list(_bodies.items()):
                bodies.append(Body(k, v))
        return bodies

    @property
    def body(self):
        """Returns a Body object of a request"""
        return self._get_body(self)

    def _get_uri_params(self, node):
        """Returns a list of URIParameter Objects"""
        uri_params = []
        if node.parent:
            uri_params = self._get_uri_params(node.parent)
        if 'uriParameters' in node.data:
            for k, v in list(node.data['uriParameters'].items()):
                uri_params.append((URIParameter(k, v)))
        return uri_params

    @property
    def uri_params(self):
        """Returns a list of URIParameter objects of a resource"""
        return self._get_uri_params(self)

    def _get_base_uri_params(self, node):
        """Returns a list of URIParameter objects for the base_uri"""
        base_uri_params = []
        if node.parent:
            base_uri_params = self._get_base_uri_params(node.parent)
        if 'baseUriParameters' in node.data:
            for k, v in list(node.data['baseUriParameters'].items()):
                base_uri_params.append((URIParameter(k, v)))
        return base_uri_params

    @property
    def base_uri_params(self):
        """Returns a list of Base URIParameter objects of a Resource"""
        return self._get_base_uri_params(self)

    def _get_query_params(self, node):
        """Returns a list of QueryParameter objects"""
        query_params = []
        if 'queryParameters' in node.data[self.method]:
            items = node.data[self.method]['queryParameters'].items()
            for k, v in list(items):
                query_params.append((QueryParameter(k, v)))
        return query_params

    @property
    def query_params(self):
        """Returns a list of QueryParameter objects"""
        return self._get_query_params(self)

    def _get_form_params(self, node):
        # TODO: abstract away the body/app/json shiz
        form_params = []
        if self.method in ['post', 'delete', 'put', 'patch']:
            if 'body' in node.data.get(self.method):
                form_headers = ['application/x-www-form-urlencoded',
                                'multipart/form-data']
                for header in form_headers:
                    form = node.data.get(self.method).get('body').get(header)
                    if form and form.get('formParameters'):
                        for k, v in list(form['formParameters'].items()):
                            form_params.append((FormParameter(k, v)))
        return form_params

    @property
    def form_params(self):
        """Returns a list of FormParameter objects"""
        return self._get_form_params(self)

    @property
    def req_content_types(self):
        """Returns a list of ContentType objects that the Resource supports"""
        content_type = []
        if self.method in ["post", "put", "delete", "patch"]:
            if self.data.get(self.method).get('body'):
                # grabs all content types
                content_types = self.data.get(self.method).get('body')
                types = self.data.get(self.method).get('body').keys()
                # TODO: skip www-form-encoded
                for content in types:
                    schema = content_types.get(content).get('schema')
                    example = content_types.get(content).get('example')
                    content_type.append(ContentType(content, schema, example))
        return content_type

    def __repr__(self):
        return "< Resource: {0} {1} >".format(self.method.upper(), self.path)
