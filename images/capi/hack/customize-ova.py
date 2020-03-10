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
import difflib
import glob
import hashlib
import io
import json
import os
from os.path import basename, dirname, join, splitext
import shutil
import sys
import tarfile

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
        self.FileSection = self.xpath(
            '/ovf:Envelope/ovf:References/ovf:File')[0]

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

    def commitManifest(self, ovfString):
        with open(join(self.ovfDir, self.mfFilename(self.ovfFilename)), 'r') as mfFile:
            lines = mfFile.readlines()
        with open(join(self.ovfDir, self.mfFilename(self.ovfFilename)), 'w') as mfFile:
            for line in lines:
                if line.startswith('SHA1(%s)=' % self.ovfFilename):
                    mfFile.write('SHA1(%s)= %s\n' %
                                 (self.ovfFilename, self.sha1(ovfString)))
                elif line.startswith('SHA256(%s)=' % self.ovfFilename):
                    mfFile.write('SHA256(%s)= %s\n' %
                                 (self.ovfFilename, self.sha256(ovfString)))
                else:
                    mfFile.write(line)

    def commit(self):
        ovfString = self.xmlToString(self.ovfXml)
        self.commitOvf(ovfString)

        self.commitManifest(ovfString)

    def getDiskName(self):
        return join(self.ovfDir, self.FileSection.get(self.nsName('ovf', 'href')))

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
        self.xpath('ovf:FullVersion', root=self.productSection)[0].text = version

    def setProduct(self, value):
        self.xpath('ovf:Product', root=self.productSection)[0].text = value

    @classmethod
    def xmlToString(cls, xml):
        return etree.tostring(xml, pretty_print=True, xml_declaration=True,
                              encoding='utf-8').decode('utf-8')

    @classmethod
    def sha1(cls, data):
        hasher = hashlib.sha1()
        hasher.update(data.encode('utf-8'))
        return hasher.hexdigest()

    @classmethod
    def sha256(cls, data):
        hasher = hashlib.sha256()
        hasher.update(data.encode('utf-8'))
        return hasher.hexdigest()

    @classmethod
    def mfFilename(cls, ovfFilename):
        return '%s.mf' % splitext(ovfFilename)[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Customize OVF metadata')

    parser.add_argument('ovf', help='path to OVA/OVF file.')

    parser.add_argument(
        '--create_ova', action='store_true',
        help='When set, create OVA when done')
    parser.add_argument(
        '--diff', action='store_true', help='Print diff of metadata')
    parser.add_argument(
        '--dry', action='store_true', help='Do not write changes to file')
    parser.add_argument(
        '--pprop_comment', action='store_true',
        help='Whether to additionally set the product property value as a text comment')
    parser.add_argument(
        '--pprop_json', action='store_true', help='Product Property value is JSON')
    parser.add_argument(
        '--pprop_key', metavar='key', help='Product Property key')
    ppv_group = parser.add_mutually_exclusive_group()
    ppv_group.add_argument(
        '--pprop_val', metavar='value', help='Product Property value')
    ppv_group.add_argument(
        '--pprop_valfile', metavar='path', help='Product Property value from a file')
    parser.add_argument(
        '--set_annotation', metavar='annotation',
        help='String value to set as product annotation')
    parser.add_argument(
        '--set_product', metavar='product',
        help='String value to set as product description')
    parser.add_argument(
        '--set_version', metavar='version',
        help='String value to set as product version')

    args = parser.parse_args()

    isOVA = False
    targetBaseDir = dirname(args.ovf)
    targetName = splitext(basename(args.ovf))[0]
    ovaExtractDir = join(targetBaseDir, '.image-builder')

    # Determine if file is OVF or OVA
    if tarfile.is_tarfile(args.ovf):
        # if OVA, extract archive and get path to OVF
        print("Path is a tar archive, treating as OVA")
        isOVA = True
        os.mkdir(ovaExtractDir)
        with tarfile.open(args.ovf) as tar:
            tar.extractall(path=ovaExtractDir)
            print("Extracted OVA to %s" % ovaExtractDir)
        customizer = OVFCustomizer(join(ovaExtractDir, '%s.ovf' % targetName))
    else:
        customizer = OVFCustomizer(args.ovf)

    if args.set_annotation is not None:
        customizer.setAnnotation(args.set_annotation)

    if args.set_product is not None:
        customizer.setProduct(args.set_product)

    if args.set_version is not None:
        customizer.setVersion(args.set_version)

    if args.pprop_key is not None:
        pprop_val = None
        if args.pprop_val is not None:
            pprop_val = args.pprop_val
        elif args.pprop_valfile is not None:
            with io.open(args.pprop_valfile, 'r', encoding='utf-8') as f:
                pprop_val = f.read()
        else:
            print("Product Propery key specified, but no value")
            sys.exit(1)

        if args.pprop_json:
            pprop_val = json.dumps(json.loads(pprop_val))

        customizer.setProductProperty(
            args.pprop_key, pprop_val, withComment=args.pprop_comment)

    if args.diff:
        print(customizer.ovfDiff())

    if not args.dry:
        customizer.commit()

    # Repackage into OVA if requested
    if args.create_ova and not args.dry:
        ovaName = args.ovf
        in_files = []
        if isOVA:
            # assume all files are already there, since we extracted an existing
            # OVA
            in_files = glob.glob(join(ovaExtractDir, '*'))
        else:
            ovaName = join(targetBaseDir, '%s.ova' % targetName)
            in_files = [args.ovf,
                        customizer.getDiskName(),
                        customizer.mfFilename(args.ovf)]
        print("creating OVA %s" % ovaName)
        with open(ovaName, 'wb') as f:
            with tarfile.open(fileobj=f, mode='w|') as tar:
                for path in in_files:
                    print("adding %s to OVA" % path)
                    tar.add(path, arcname=basename(path))

    # Clean up OVA dir unless we are not repackaging
    if isOVA and args.create_ova:
        shutil.rmtree(ovaExtractDir)
        print("removed directory %s" % ovaExtractDir)
