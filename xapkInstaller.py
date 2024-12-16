#! /usr/bin/python3
# coding: utf-8
import logging
import os
import shutil
import subprocess
import sys
import argparse
import traceback

from androguard.core.axml import AXMLPrinter
from chardet import detect
from defusedxml.minidom import parseString
from hashlib import md5 as _md5
from json import load as json_load
from re import findall as re_findall
from shlex import split as shlex_split
from typing import List, NoReturn, Tuple, Union
from yaml import safe_load
from zipfile import ZipFile


_abi = ["armeabi_v7a", "arm64_v8a", "armeabi", "x86_64", "x86", "mips64", "mips"]
_language = ["ar", "bn", "de", "en", "et", "es", "fr", "hi", "in", "it",
             "ja", "ko", "ms", "my", "nl", "pt", "ru", "sv", "th", "tl",
             "tr", "vi", "zh"]
info_msg = {
    "bundletool": "https://github.com/google/bundletool/releases"
                  "Download and rename to bundletool.jar"
                  "And place it in the same folder as xapkInstaller.",
    "sdktoolow": "Installation failed: Android version is too low!"
}


def tostr(bytes_: bytes) -> str:
    return bytes_.decode(detect(bytes_)["encoding"])


class Device:
    __slots__ = ['ADB', '_abi', '_abilist', '_dpi', '_drawable', '_locale', '_sdk', 'device']

    def __init__(self, device: str = None):
        self.ADB = 'adb'
        self._abi = None
        self._abilist = None
        self._dpi = None
        self._drawable = None
        self._locale = None
        self._sdk = None
        self.device = device

    @property
    def abi(self) -> str:
        if not self._abi:
            self._abi = self.shell(['getprop', 'ro.product.cpu.abi'])[1].strip()
        return self._abi

    @property
    def abilist(self) -> list:
        if not self._abilist:
            self._abilist = self.shell(['getprop', 'ro.product.cpu.abilist'])[1].strip().split(",")
        return self._abilist

    @property
    def dpi(self) -> int:
        if not self._dpi:
            self.getdpi()
        return self._dpi

    def getdpi(self) -> int:
        _dpi = self.shell(['dumpsys', 'window', 'displays'])[1]
        for i in _dpi.strip().split("\n"):
            if i.find("dpi") >= 0:
                for j in i.strip().split(" "):
                    if j.endswith("dpi"):
                        self._dpi = int(j[:-3])
        return self._dpi

    @property
    def drawable(self) -> list:
        if not self._drawable:
            self.getdrawable()
        return self._drawable

    def getdrawable(self) -> list:
        _dpi = int((self.dpi+39)/40)
        if 0 <= _dpi <= 3:
            self._drawable = ["ldpi"]
        elif 3 < _dpi <= 4:
            self._drawable = ["mdpi"]
        elif 4 < _dpi <= 6:
            self._drawable = ["tvdpi", "hdpi"]
        elif 6 < _dpi <= 8:
            self._drawable = ["xhdpi"]
        elif 8 < _dpi <= 12:
            self._drawable = ["xxhdpi"]
        elif 12 < _dpi <= 16:
            self._drawable = ["xxxhdpi"]
        return self._drawable

    @property
    def locale(self) -> str:
        if not self._locale:
            self._locale = self.shell(['getprop', 'ro.product.locale'])[1].strip().split('-')[0]
        return self._locale

    @property
    def sdk(self) -> int:
        if not self._sdk:
            self.getsdk()
        return self._sdk

    def getsdk(self) -> int:
        __sdk = ["ro.build.version.sdk", "ro.product.build.version.sdk",
                 "ro.system.build.version.sdk", "ro.system_ext.build.version.sdk"]
        for i in __sdk:
            _sdk = self.shell(['getprop', i])[1].strip()
            if _sdk:
                self._sdk = int(_sdk)
                return self._sdk

    # ===================================================
    def adb(self, cmd: list):
        c = [self.ADB]
        if self.device:
            c.extend(['-s', self.device])
        c.extend(cmd)
        return run_msg(c)

    def shell(self, cmd: list):
        c = ['shell']
        c.extend(cmd)
        return self.adb(c)

    # ===================================================
    def _abandon(self, SESSION_ID: str):
        """abort installation"""
        run, msg = self.shell(["pm", "install-abandon", SESSION_ID])
        if msg:
            logger.info(msg)

    def _commit(self, SESSION_ID: str):
        run, msg = self.shell(["pm", "install-commit", SESSION_ID])
        if run.returncode:
            self._abandon(SESSION_ID)
            sys.exit(msg)
        else:
            logger.info(msg)
        return run

    def _create(self) -> str:
        run, msg = self.shell(["pm", "install-create"])
        if run.returncode:
            sys.exit(msg)
        logger.info(msg)  # Success: created install session [1234567890]
        return msg.strip()[:-1].split("[")[1]

    def _del(self, info):
        for i in info:
            run, msg = self.shell(["rm", i["path"]])
            if run.returncode:
                sys.exit(msg)

    def _push(self, file_list: list) -> List[dict]:
        info = []
        for f in file_list:
            info.append({"name": "_".join(f.rsplit(".")[:-1]), "path": "/data/local/tmp/"+f})
            run, msg = self.adb(["push", f, info[-1]["path"]])
            if run.returncode:
                sys.exit(msg)
        return info

    def _write(self, SESSION_ID: str, info: list):
        index = 0
        for i in info:
            # pm install-write SESSION_ID SPLIT_NAME PATH
            run, msg = self.shell(["pm", "install-write",
                                   SESSION_ID, i["name"], i["path"]])
            if run.returncode:
                self._abandon(SESSION_ID)
                sys.exit(msg)
            index += 1


def build_apkm_config(device: Device, file_list: List[str], install: List[str]) -> Tuple[dict, List[str]]:
    abi = [f"split_config.{i}.apk" for i in _abi]
    language = [f"split_config.{i}.apk" for i in _language]
    config = {"language": []}
    for i in file_list:
        if i == f"split_config.{device.abi.replace('-', '_')}.apk":
            config["abi"] = i
        for d in device.drawable:
            if i == f"split_config.{d}.apk":
                config["drawable"] = i
        if i == f"split_config.{device.locale}.apk":
            config["language"].insert(0, i)
        elif (i in abi) or (i.find("dpi.apk") >= 0):
            config[i.split(".")[1]] = i
        elif i in language:
            config["language"].append(i)
        elif i.endswith(".apk"):
            install.append(i)
    logger.info(config)
    return config, install


def build_xapk_config(device: Device, split_apks: List[dict], install: List[str]):
    abi = [f"config.{i}" for i in _abi]
    language = [f"config.{i}" for i in _language]
    config = {"language": []}
    for i in split_apks:
        if i["id"] == f"config.{device.abi.replace('-', '_')}":
            config["abi"] = i["file"]
        for d in device.drawable:
            if i == f"split_config.{d}.apk":
                config["drawable"] = i
        if i["id"] == f"config.{device.locale}":
            config["language"].insert(0, i["file"])
        elif (i["id"] in abi) or i["id"].endswith("dpi"):
            config[i["id"].split(".")[1]] = i["file"]
        elif i["id"] in language:
            config["language"].append(i["file"])
        else:
            install.append(i["file"])
    logger.info(config)
    return config, install


def check(ADB=None) -> List[str]:
    if not ADB:
        ADB = check_sth('adb')
    run, msg = run_msg([ADB, 'devices'])
    _devices = msg.strip().split("\n")[1:]
    if _devices == ['* daemon started successfully']:
        logger.info('Start adb service for the first time')
        run, msg = run_msg([ADB, 'devices'])
        _devices = msg.strip().split("\n")[1:]
    devices = []
    for i in _devices:
        if i.split("\t")[1] != "offline":
            devices.append(i.split("\t")[0])

    # adb -s <device-id/ip:port> shell xxx
    if run.returncode:
        logger.error(msg)
    elif len(devices) == 0:
        logger.error("The phone is not connected to the PC!")
    elif len(devices) == 1:
        pass
    elif len(devices) > 1:
        logger.info('More than 1 device detected, multi-device installation will be performed!')
    return devices


def check_sth(key, conf='config.yaml'):
    if key not in ['adb', 'java', 'aapt', 'bundletool']:
        return None
    conf = read_yaml(conf)
    path = conf.get(key, key)
    if not os.path.exists(path):
        # When configuration file is wrong or empty, use adb in the system environment
        try:
            if key in ['adb', 'java']:
                run, msg = run_msg([key, '--version'])
            elif key in ['aapt']:
                run, msg = run_msg([key, 'v'])
            elif key in ['bundletool']:
                run, msg = run_msg([check_sth('java'), '-jar', key+'.jar', 'version'])
        except FileNotFoundError:
            run = None
        if run and (run.returncode == 0):
            logger.info(f'check_sth({key!r})')
            logger.info(msg.strip())
            return key
        logger.error(f'Not configured: {key}')
        return None
    return path


def check_by_manifest(device: Device, manifest: dict) -> None:
    if device.sdk < manifest["min_sdk_version"]:
        sys.exit(info_msg['sdktoolow'])
    else:
        try:
            if device.sdk > manifest["target_sdk_version"]:
                logger.warning("Android version is too high! There may be compatibility issues!")
        except KeyError:
            logger.warning("`manifest['target_sdk_version']` no found.")

    abilist = device.abilist

    try:
        if manifest.get("native_code") and not findabi(manifest["native_code"], abilist):
            sys.exit(f"Installation failed: {manifest['native_code']}\nApplication binary interface (abi) mismatch! Supported abi list by this phone: {abilist}")
    except UnboundLocalError:
        logger.exception('Failed in check_by_manifest->findabi.')


def checkVersion(device: Device, package_name: str, fileVersionCode: int, versionCode: int = -1, abis: list = []) -> None:
    msg = device.shell(["pm", "dump", package_name])[1]
    fileVersionCode = int(fileVersionCode)
    for i in msg.split("\n"):
        if "versionCode" in i:
            versionCode = int(i.strip().split("=")[1].split(" ")[0])
            if versionCode == -1:
                input("WARNING: For the first installation, you need to click on the mobile phone to allow the installation!\nPress Enter to continue")
            elif fileVersionCode < versionCode:
                if input("WARNING: Downgrade installation? Please make sure the file is correct! (y/N)").lower() != "y":
                    sys.exit("To downgrade the installation, the user cancels the installation.")
            elif fileVersionCode == versionCode:
                if input("WARNING: The version is consistent! Please make sure the file is correct! (y/N)").lower() != "y":
                    sys.exit("The versions match, and the user cancels the installation.")
        elif "primaryCpuAbi" in i:
            primaryCpuAbi = i.strip().split("=")[1]
            if (primaryCpuAbi == 'arm64-v8a' and abis) and (primaryCpuAbi not in abis):
                if input("Warning: Changing from 64-bit to 32-bit? Please make sure the file is correct! (y/N)").lower() != "y":
                    sys.exit("The user cancels the installation.")


def config_abi(config: dict, install: List[str], abilist: List[str]):
    if config.get("abi"):
        install.append(config["abi"])
    else:
        for i in abilist:
            i = i.replace('-', '_')
            if config.get(i):
                install.append(config[i])
                break
    return config, install


def config_drawable(config: dict, install: List[str]):
    if config.get("drawable"):
        install.append(config["drawable"])
    else:
        _drawableList = ["xxxhdpi", "xxhdpi", "xhdpi", "hdpi", "tvdpi", "mdpi", "ldpi", "nodpi"]
        for i in _drawableList:
            if config.get(i):
                install.append(config[i])
                break
    return config, install


def config_language(config: dict, install: List[str]):
    if config.get("language"):
        # If there is a language pack with the same device language, it will be installed first
        install.append(config["language"][0])
    else:
        logger.warning("Could not find any of the language packs!!")
    return config, install


def copy_files(copy: List[str]):
    logger.info(f"Copying '{copy[0]}' to '{copy[1]}'")
    if os.path.exists(copy[1]):
        delPath(copy[1])
    if os.path.isfile(copy[0]):
        shutil.copyfile(copy[0], copy[1])
    else:
        shutil.copytree(copy[0], copy[1])


def delPath(path: str):
    if not os.path.exists(path):
        return
    logger.info(f"Delete {path}")
    if os.path.isfile(path):
        return os.remove(path)
    return shutil.rmtree(path)


def dump(file_path: str, del_path: List[str]) -> dict:
    run, msg = run_msg(["aapt", "dump", "badging", file_path])
    if msg:
        logger.info(msg)
    if run.returncode:
        logger.warning("aapt is not configured or there is an error with aapt!")
        return dump_py(file_path, del_path)
    manifest = {"native_code": []}
    for line in msg.split("\n"):
        if "sdkVersion:" in line:
            manifest["min_sdk_version"] = int(line.strip().split("'")[1])
        elif "targetSdkVersion:" in line:
            manifest["target_sdk_version"] = int(line.strip().split("'")[1])
        elif "native-code:" in line:
            manifest["native_code"].extend(re_findall(r"'([^,']+)'", line))
        elif "package: name=" in line:
            line = line.strip().split("'")
            manifest["package_name"] = line[1]
            try:
                manifest["versionCode"] = int(line[3])
            except ValueError:
                logger.error(f"err in dump: ValueError: line[3]: {line[3]!r}")
                manifest["versionCode"] = 0
    return manifest


def dump_py(file_path: str, del_path: List[str]) -> dict:
    del_path.append(os.path.join(os.getcwd(), get_unpack_path(file_path)))
    zip_file = ZipFile(file_path)
    upfile = "AndroidManifest.xml"
    zip_file.extract(upfile, del_path[-1])
    with open(os.path.join(del_path[-1], upfile), "rb") as f:
        data = f.read()
    ap = AXMLPrinter(data)
    buff = parseString(ap.getBuff())
    manifest = {}
    _manifest = buff.getElementsByTagName("manifest")[0]
    uses_sdk = buff.getElementsByTagName("uses-sdk")[0]
    manifest["package_name"] = _manifest.getAttribute("package")
    manifest["versionCode"] = int(_manifest.getAttribute("android:versionCode"))
    manifest["min_sdk_version"] = int(uses_sdk.getAttribute("android:minSdkVersion"))
    try:
        manifest["target_sdk_version"] = int(uses_sdk.getAttribute("android:targetSdkVersion"))
    except ValueError:
        logger.warning("`targetSdkVersion` no found.")
    file_list = zip_file.namelist()
    native_code = []
    for i in file_list:
        if i.startswith("lib/"):
            native_code.append(i.split("/")[1])
    manifest["native_code"] = list(set(native_code))
    return manifest


def findabi(native_code: List[str], abilist: List[str]) -> bool:
    for i in abilist:
        if i in native_code:
            return True
    return False


def get_unpack_path(file_path: str) -> str:
    """Get file decompression path"""
    dir_path, name_suffix = os.path.split(file_path)
    name = os.path.splitext(name_suffix)[0]
    unpack_path = os.path.join(dir_path, name)
    return unpack_path


def install_aab(device: Device, file: str, del_path: List[str], root: str) -> Tuple[List[str], bool]:
    """The official version needs to be signed, and it can be installed after configuration"""
    logger.info(install_aab.__doc__)
    name_suffix = os.path.split(file)[1]
    name = name_suffix.rsplit(".", 1)[0]
    del_path.append(name+".apks")
    if os.path.exists(del_path[-1]):
        delPath(del_path[-1])
    build = ["java", "-jar", "bundletool.jar", "build-apks",
             "--connected-device", "--bundle="+name_suffix,
             "--output="+del_path[-1]]
    sign = read_yaml("./config.yaml")
    if sign.get("ks") and sign.get("ks-pass") and sign.get("ks-key-alias") and sign.get("key-pass"):
        for i in sign:
            build.append(f"--{i}={sign[i]}")
    run = run_msg(build)[0]
    if run.returncode:
        sys.exit(info_msg['bundletool'])
    return install_apks(device, del_path[-1], del_path, root)


def install_apk(device: Device, file: str, del_path: List[str], root: str, abc: str = "-rtd") -> Tuple[List[str], bool]:
    """Install the apk file"""
    name_suffix: str = os.path.split(file)[1]
    manifest = dump(name_suffix, del_path)
    logger.info(manifest)
    checkVersion(device, manifest["package_name"], int(manifest["versionCode"]), manifest["native_code"])
    check_by_manifest(device, manifest)

    install = ["install", abc, name_suffix]
    run, msg = device.adb(install)
    if run.returncode:
        if abc == "-rtd":
            if "argument expected" in msg:
                logger.error('No argument expected after "-rtd"')
            else:  # WSA
                logger.error(f'{msg!r}')
            logger.info("Modifying installation parameters to reinstall, please wait")
            return install_apk(device, file, del_path, root, "-r")
        elif abc == "-r":
            if uninstall(device, manifest["package_name"], root):
                return install_apk(device, file, del_path, root, "")
        elif "INSTALL_FAILED_TEST_ONLY" in msg:
            logger.error('INSTALL_FAILED_TEST_ONLY')
            logger.info("Modifying installation parameters to reinstall, please wait")
            return install_apk(device, file, del_path, root, "-t")
        else:
            sys.exit(1)
    return install, True


def install_apkm(device: Device, file: str, del_path: List[str], root: str) -> Tuple[List[str], bool]:
    del_path.append(os.path.join(os.getcwd(), get_unpack_path(file)))
    zip_file = ZipFile(file)
    upfile = "info.json"
    zip_file.extract(upfile, del_path[-1])
    info = read_json(os.path.join(del_path[-1], upfile))
    file_list = zip_file.namelist()
    if device.sdk < int(info["min_api"]):
        sys.exit(info_msg['sdktoolow'])
    checkVersion(device, info["pname"], info["versioncode"], info["arches"])
    install = ["install-multiple", "-rtd"]
    config, install = build_apkm_config(device, file_list, install)
    config, install = config_abi(config, install, device.abilist)
    config, install = config_drawable(config, install)
    config, install = config_language(config, install)
    for i in install[5:]:
        zip_file.extract(i, del_path[-1])
    os.chdir(del_path[-1])
    return install_multiple(device, install)


def install_apks(device: Device, file: str, del_path: List[str], root: str) -> Tuple[List[str], bool]:
    os.chdir(root)
    zip_file = ZipFile(file)
    file_list = zip_file.namelist()
    if 'toc.pb' not in file_list:
        if 'meta.sai_v2.json' in file_list:  # SAI v2
            return install_apks_sai(device, file, del_path, version=2)
        elif 'meta.sai_v1.json' in file_list:  # SAI v1
            return install_apks_sai(device, file, del_path, version=1)
        else:  # unknow
            return [], False
    try:
        install, status = install_apks_java(file)
        if status:
            return install, status
    except FileNotFoundError:
        pass
    logger.warning('If java environment is not configured or there is an error, it will try to parse the file directly')
    return install_apks_py(device, file, del_path)


def install_apks_java(file: str) -> Tuple[List[str], bool]:
    name_suffix: str = os.path.split(file)[1]
    install = ["java", "-jar", "bundletool.jar", "install-apks", "--apks="+name_suffix]
    run, msg = run_msg(install)
    if run.returncode:
        if '[SCREEN_DENSITY]' in msg:
            sys.exit("Missing APKs for [SCREEN_DENSITY] dimensions in the module 'base' for the provided device.")
        else:
            sys.exit(info_msg['bundletool'])
    return install, True


def install_apks_py(device: Device, file: str, del_path: List[str]) -> Tuple[List[str], bool]:
    zip_file = ZipFile(file)
    file_list = zip_file.namelist()
    del_path.append(os.path.join(os.getcwd(), get_unpack_path(file)))
    if device.sdk < 21:
        logger.warning('The current Android version does not support multi-apk mode installation, I hope there is a suitable standalone file in the apks')
        for i in file_list:
            f = None
            if f'standalone-{device.abi}_{device.dpi}.apk' in i:
                f = zip_file.extract(i, del_path[-1])
            else:
                for a in device.abilist:
                    for d in device.drawable:
                        if f'standalone-{a}_{d}.apk' in i:
                            f = zip_file.extract(i, del_path[-1])
            if f:
                return install_apk(device, f, del_path, os.getcwd())
            logger.error('看来没有')
            sys.exit('No suitable standalone file')
    install = ["install-multiple", ""]
    for i in file_list:
        if i.startswith('splits/'):
            install.append(zip_file.extract(i, del_path[-1]))
    return install_multiple(device, install)


def install_apks_sai(device: Device, file: str, del_path: List[str], version: int) -> Tuple[List[str], bool]:
    """Used to install the apks file generated by SAI"""
    del_path.append(os.path.join(os.getcwd(), get_unpack_path(file)))
    zip_file = ZipFile(file)
    file_list = zip_file.namelist()
    for i in ['meta.sai_v2.json', 'meta.sai_v1.json', 'icon.png']:
        try:
            file_list.remove(i)
        except ValueError:
            pass
    install = ['']
    if version == 2:
        upfile = "meta.sai_v2.json"
        zip_file.extract(upfile, del_path[-1])
        data = read_json(os.path.join(del_path[-1], upfile))
        checkVersion(device, data['package'], data['version_code'])

        if data.get('split_apk'):
            install = ["install-multiple", ""]
            install.extend(file_list)
            return install_multiple(device, install)
        else:
            install = ["install", "", file_list[0]]
            run, msg = device.adb(install)
            if run.returncode:
                logger.error(msg)
                return install, False
            return install, True
    elif version == 1:
        logger.error('Undone')
        return install, False
    else:
        logger.error('Unknown situation')
        return install, False


def install_base(device: Device, file_list: List[str]) -> Tuple[List[dict], bool]:
    SESSION_ID = device._create()
    info = device._push(file_list)
    device._write(SESSION_ID, info)
    run = device._commit(SESSION_ID)
    device._del(info)
    if run.returncode:
        return info, False
    return info, True


def install_multiple(device: Device, install: List[str]) -> Tuple[List[str], bool]:
    """install-multiple"""
    run = device.adb(install)[0]
    if run.returncode:
        if install[1] == '-rtd':
            install[1] = '-r'
            logger.info("Modifying installation parameters to reinstall, please wait")
            return install_multiple(device, install)
        elif install[1] == 'r':
            install[1] = ''
            logger.info("Modifying installation parameters to reinstall, please wait")
            return install_multiple(device, install)
        elif install[1] == '':
            print_err(tostr(run.stderr))
            try:
                logger.info("Use alternatives")
                run = install_base(device, install[2:])[1]
                if not run.returncode:
                    return install, run
            except Exception:
                logger.exception('Failed in install_multiple->install_base.')
    return install, run


def install_xapk(device: Device, file: str, del_path: List[str], root: str) -> Tuple[List[Union[str, List[str]]], bool]:
    """install xapk file"""
    os.chdir(file)
    logger.info("Start installation")
    if not os.path.isfile("manifest.json"):
        sys.exit(f"Installation failed: No `manifest.json` in {file!r} Not the decompression path of `xapk` installation package!")
    manifest = read_json("manifest.json")
    if not manifest.get("expansions"):
        split_apks = manifest["split_apks"]

        if not args.ignore:
            if device.sdk < int(manifest["min_sdk_version"]):
                sys.exit(info_msg['sdktoolow'])
            elif device.sdk > int(manifest["target_sdk_version"]):
                logger.info("Android version is too high! There may be compatibility issues!")

        install = ["install-multiple", "-rtd"]
        config, install = build_xapk_config(device, split_apks, install)
        checkVersion(device, manifest["package_name"], manifest["version_code"], config.get('abi'))
        config, install = config_abi(config, install, device.abilist)
        config, install = config_drawable(config, install)
        config, install = config_language(config, install)
        return install_multiple(device, install)
    else:
        install = install_apk(device, manifest["package_name"]+".apk", del_path, root)[0]
        expansions = manifest["expansions"]
        for i in expansions:
            if i["install_location"] == "EXTERNAL_STORAGE":
                push: List[str] = ["push", i["file"], "/storage/emulated/0/"+i["install_path"]]
                if device.adb(push)[0].returncode:
                    return [install, push], False
                return [install, push], True
            else:
                sys.exit(1)


# device: Device, file: str, del_path: List[str], root: str[, abc: str] -> Tuple[List[Union[str, List[str]]], bool]
installSuffix = [".aab", ".apk", ".apkm", ".apks", ".xapk"]
installSelector = {".aab": install_aab, ".apk": install_apk, ".apkm": install_apkm, ".apks": install_apks,
                   ".xapk": install_xapk}


def main(root: str, one: str) -> bool:
    os.chdir(root)
    name_suffix = os.path.split(one)[1]
    name_suffix = name_suffix.rsplit(".", 1)
    new_path = md5(name_suffix[0])  # md5: Avoid unexpected problems caused by inexplicable file names
    if len(name_suffix) > 1:
        new_path += f".{name_suffix[1]}"
    del_path = [os.path.join(root, new_path)]
    copy = [one, del_path[0]]
    copy_files(copy)

    try:
        ADB = check_sth('adb')
        devices = check(ADB)
        if len(devices) == 0:
            sys.exit("Installation failed: The phone is not connected to PC!")
        suffix = os.path.splitext(os.path.split(copy[1])[1])[1]

        for device in devices:
            if args.serial in device:
                device = Device(device)
                device.ADB = ADB
                if copy[1].endswith(".xapk"):
                    del_path.append(unpack(copy[1]))
                    os.chdir(del_path[-1])
                elif suffix in installSuffix:
                    return installSelector[suffix](device, copy[1], del_path, root)[1]
                elif os.path.isfile(copy[1]):
                    sys.exit(f"{copy[1]!r} no `{'/'.join(installSuffix)}` Installation package!")

                if os.path.isdir(del_path[-1]) and os.path.exists(os.path.join(del_path[-1], "manifest.json")):
                    os.chdir(del_path[-1])
                    install, run = install_xapk(device, del_path[-1], del_path, root)
                    if run.returncode:
                        print_err(tostr(run.stderr))
                        try:
                            logger.info("use alternatives")
                            run = install_base(device, install[5:])[1]
                            if not run.returncode:
                                return True
                        except Exception:
                            logger.exception('Failed in main->install_base.')
                        if input("Installation failed! Will try to keep the data to uninstall and reinstall, it may take more time, continue? (y/N)").lower() == 'y':
                            package_name: str = read_json(os.path.join(del_path[-1], "manifest.json"))["package_name"]
                            if uninstall(device, package_name, root):
                                for i in install:
                                    run, msg = run_msg(i)
                                    if run.returncode:
                                        sys.exit(msg)
                        else:
                            sys.exit("User canceled installation!")
        return True
    except SystemExit as err:
        if err.code == 1:
            logger.error("Error Installation failed: unknown error! Please provide files for adaptation!")
        elif err.code != 0:
            logger.error(err)
        return False
    except Exception:
        logger.exception('Failed in main.')
        return False
    finally:
        os.chdir(root)
        for i in del_path:
            delPath(i)


def md5(_str: str, encoding='utf-8') -> str:
    m = _md5()
    _str = _str.encode(encoding)
    m.update(_str)
    return m.hexdigest()


def pause() -> NoReturn:
    input("Press enter to continue")
    sys.exit(0)


def print_err(err: str):
    if "INSTALL_FAILED_VERSION_DOWNGRADE" in err:
        logger.warning("Downgrade installation? Please make sure the file is correct!")
    elif "INSTALL_FAILED_USER_RESTRICTED: Install canceled by user" in err:
        sys.exit("The user canceled the installation or did not confirm the installation!\nThe initial installation needs manual confirmation!!")
    elif "INSTALL_FAILED_ALREADY_EXISTS" in err:
        sys.exit("An application with the same package name and version number has been installed!!")
    else:
        logger.error(err)


def pull_apk(device: Device, package: str, root: str) -> str:
    logger.info("Backing up installation package")
    run, msg = device.shell(["pm", "path", package])
    if run.returncode:
        sys.exit(msg)
    else:
        dir_path = os.path.join(root, package)
        if os.path.exists(dir_path):
            delPath(dir_path)
        os.mkdir(dir_path)
        try:
            for i in tostr(run.stdout).strip().split("\n"):
                run, msg = device.adb(["pull", i[8:].strip(), dir_path])
                if run.returncode:
                    sys.exit(msg)
        except TypeError:
            logger.exception('Failed in pull_apk.')
            sys.exit(1)
        cmd = ["pull", "/storage/emulated/0/Android/obb/"+package, dir_path]
        run, msg = device.adb(cmd)
        if run.returncode and ("No such file or directory" not in msg) and ("does not exist" not in msg):
            sys.exit(msg)
        return dir_path


def read_yaml(file) -> dict:
    if not os.path.exists(file):
        return {}
    with open(file, "rb") as f:
        data = f.read()
    return safe_load(tostr(data))


def read_json(file) -> dict:
    with open(file) as f:
        return json_load(f)


def restore(device: Device, dir_path: str, root: str):
    logger.info("Start recovery")
    os.chdir(dir_path)
    all_file = os.listdir(dir_path)
    obb = False
    for i in all_file:
        if i.endswith(".obb"):
            obb = True
            break
    if obb:
        for i in all_file:
            if i.endswith(".apk"):
                install_apk(device, os.path.join(dir_path, i), [], root)
            elif i.endswith(".obb"):
                push = ["push", os.path.join(dir_path, i),
                        "/storage/emulated/0/Android/obb/"+os.path.split(dir_path)[-1]]
                device.adb(push)
    else:
        if len(all_file) == 0:
            sys.exit("backup folder is empty!")
        elif len(all_file) == 1:
            main(root, all_file[0])
        elif len(all_file) > 1:
            install = ["install-multiple", "-rtd"]
            install.extend(all_file)
            install_multiple(device, install)
    os.chdir(root)


def run_msg(cmd: Union[str, List[str]]):
    if type(cmd) is str:
        cmd = shlex_split(cmd)
    run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.stderr:
        return run, tostr(run.stderr)
    if run.stdout:
        return run, tostr(run.stdout)
    return run, str()


def uninstall(device: Device, package_name: str, root: str):
    dir_path = pull_apk(device, package_name, root)
    if not dir_path:
        sys.exit("An error occurred while backing up the file")
    # adb uninstall package_name
    logger.info("Start uninstall")
    run = device.shell(["pm", "uninstall", "-k", package_name])[0]
    try:
        if run.returncode:
            restore(device, dir_path, root)
    except Exception:
        logger.exception('Failed in uninstall->restore.')
        sys.exit(f"An unknown error occurred while restoring! Please try to manually operate and report the problem!\nOld version installation package path: {dir_path}")
    return run


def unpack(file: str) -> str:
    """unzip files"""
    unpack_path = get_unpack_path(file)
    logger.info("xapk found, the decompression can be slow, please be patient")
    shutil.unpack_archive(file, unpack_path, "zip")
    return unpack_path

def SetupParameters():
    parser = argparse.ArgumentParser(description='xapkInstaller - Android Universal File Installer')
    parser.add_argument('-f', '--file', nargs='+', dest='file', help='filepath or dirpath', required=True)
    parser.add_argument('-s', '--serial', dest='serial', default='', help='set single device with given serial')
    parser.add_argument('-i', '--ignore', dest='ignore', action='store_true', default=False, help='ignore small errors')
    parser.add_argument('--debug', action='store_true', dest='debug', help='debug option')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    try:
        # arguments
        args = SetupParameters()

        # log setting
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        handler = logging.NullHandler()
        if args.debug:
            handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s:%(levelname)s: %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

        # main code
        rootdir = os.path.split(sys.argv[0])[0]
        if not rootdir:
            rootdir = os.getcwd()
        _len_ = len(args.file)
        success = 0

        for _i, _one in enumerate(args.file):
            _one = os.path.abspath(os.path.join(os.getcwd(), _one))
            logger.info(f"Installing {_i+1}/{_len_} - {str(_one)}")
            if main(rootdir, _one):
                success += 1
                
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
    except:
        logger.error(traceback.print_exc())
