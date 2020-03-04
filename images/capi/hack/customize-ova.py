#!/usr/bin/env python

# Copyright 2020 The Kubernetes Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import hashlib
import difflib
from os.path import basename, dirname, join, splitext

from lxml import etree


class OVFCustomizer(object):
    """Basic functionality to manipulate OVF and related files"""

    def __init__(self, ovfPath):
        self.ovfDir = dirname(ovfPath)
        self.ovfFilename = basename(ovfPath)

        self.ovfXml = self.parseOvfXml(join(self.ovfDir, self.ovfFilename))
        self.nsMap = {k: v for (k, v) in self.ovfXml.getroot(
        ).nsmap.items() if k is not None}

        self.productSection = self.xpath(
            '/ovf:Envelope/ovf:VirtualSystem/ovf:ProductSection')[0]
        self.vhwSection = self.xpath(
            '/ovf:Envelope/ovf:VirtualSystem/ovf:VirtualHardwareSection')[0]
        self.annoSection = self.xpath(
            '/ovf:Envelope/ovf:VirtualSystem/ovf:AnnotationSection')[0]

    def parseOvfXml(self, ovfFilename):
        with open(ovfFilename, 'rb') as ovfFile:
            return etree.parse(ovfFile, etree.XMLParser(remove_blank_text=True))

    def nsName(self, ns, name):
        return '{%s}%s' % (self.nsMap[ns], name)

    def xpath(self, path, root=None):
        root = root if root is not None else self.ovfXml
        return root.xpath(path, namespaces=self.nsMap)

    def xpathRemove(self, path, root=None):
        for element in self.xpath(path, root):
            element.getparent().remove(element)

    def ovfDiff(self):
        origOvf = self.xmlToString(self.parseOvfXml(
            join(self.ovfDir, self.ovfFilename)))
        curOvf = self.xmlToString(self.ovfXml)
        diffGen = difflib.unified_diff(
            origOvf.splitlines(), curOvf.splitlines())
        return '\n'.join(list(diffGen))

    def commitOvf(self, ovfString):
        with open(join(self.ovfDir, self.ovfFilename), 'w') as ovfFile:
            ovfFile.write(ovfString)

    def commitManifest(self, ovfHash):
        with open(join(self.ovfDir, self.mfFilename(self.ovfFilename)), 'r') as mfFile:
            lines = mfFile.readlines()
        with open(join(self.ovfDir, self.mfFilename(self.ovfFilename)), 'w') as mfFile:
            for line in lines:
                if line.startswith('SHA256(%s)=' % self.ovfFilename):
                    mfFile.write('SHA256(%s)= %s\n' %
                                 (self.ovfFilename, ovfHash))
                else:
                    mfFile.write(line)

    def commit(self):
        ovfString = self.xmlToString(self.ovfXml)
        self.commitOvf(ovfString)

        ovfHash = self.sha1(ovfString)
        self.commitManifest(ovfHash)

    def setProductProperty(self, key, value, type='string',
                           userConfigurable=False, withComment=False):
        self.xpathRemove('ovf:Property[@ovf:key="%s"]' %
                         key, root=self.productSection)
        prop = etree.SubElement(self.productSection,
                                self.nsName('ovf', 'Property'))
        prop.attrib.update({
            self.nsName('ovf', 'key'): key,
            self.nsName('ovf', 'value'): value,
            self.nsName('ovf', 'type'): type,
            self.nsName('ovf', 'userConfigurable'): 'true' if userConfigurable else 'false'
        })
        if withComment:
            prop.append(etree.Comment(text='value=%s' % value))

    def setExtraConfig(self, key, value, required=False):
        self.xpathRemove(
            'vmw:ExtraConfig[@vmw:key="%s"]' % key, root=self.vhwSection)
        prop = etree.SubElement(
            self.vhwSection, self.nsName('vmw', 'ExtraConfig'))
        prop.attrib.update({
            self.nsName('vmw', 'key'): key,
            self.nsName('vmw', 'value'): value,
            self.nsName('ovf', 'required'): 'true' if required else 'false'
        })

    def setAnnotation(self, value):
        self.xpathRemove('ovf:Annotation', root=self.annoSection)
        prop = etree.SubElement(
            self.annoSection, self.nsName('ovf', 'Annotation'))
        prop.text = value

    def setVersion(self, version):
        self.xpath('ovf:Version', root=self.productSection)[0].text = version
        self.xpath('ovf:FullVersion', root=self.productSection)[
                   0].text = version

    def setProduct(self, value):
        self.xpath('ovf:Product', root=self.productSection)[0].text = value

    @classmethod
    def xmlToString(cls, xml):
        return etree.tostring(xml, pretty_print=True, xml_declaration=True,
                              encoding='utf-8').decode('utf-8')

    @classmethod
    def sha256(cls, data):
        hasher = hashlib.sha256()
        hasher.update(data.encode('utf-8'))
        return hasher.hexdigest()

    @classmethod
    def mfFilename(cls, ovfFilename):
        return '%s.mf' % splitext(ovfFilename)[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Customize OVF')

    parser.add_argument('ovf', help='OVF file')
    parser.add_argument(
        '--dry', action='store_true', help='Do not write changes to file')
    parser.add_argument(
        '--diff', action='store_true', help='Print diff of metadata')

    args = parser.parse_args()

    customizer = OVFCustomizer(args.ovf)

    if args.diff:
        print(customizer.ovfDiff())

    if not args.dry:
        customizer.commit()
