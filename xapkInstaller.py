import json
import os
import shutil
import subprocess
import sys
import traceback


class Device:
    @property
    def abi(self):
        return os.popen("adb shell getprop ro.product.cpu.abi").read().strip()
    
    @property
    def abilist(self):
        return os.popen("adb shell getprop ro.product.cpu.abilist").read().strip()
    
    @property
    def locale(self):
        return os.popen("adb shell getprop ro.product.locale").read().strip()
    
    @property
    def sdk(self):
        _sdk = os.popen("adb shell getprop ro.build.version.sdk").read().strip()
        if not _sdk:
            _sdk = os.popen("adb shell getprop ro.product.build.version.sdk").read().strip()
        if not _sdk:
            _sdk = os.popen("adb shell getprop ro.system.build.version.sdk").read().strip()
        if not _sdk:
            _sdk = os.popen("adb shell getprop ro.system_ext.build.version.sdk").read().strip()
        return int(_sdk)


def unpack(file_path):
    """解压xapk文件"""
    (dir_path, name_suffix) = os.path.split(file_path)
    (name, suffix) = os.path.splitext(name_suffix)
    unpack_path = os.path.join(dir_path, name)
    shutil.unpack_archive(file_path, unpack_path, "zip")
    return unpack_path

def uninstall_xapk(file_path):
    package_name = read_manifest("manifest.json")["package_name"]
    uninstall = ["adb", "uninstall", package_name]
    print(uninstall)
    return subprocess.call(uninstall, shell=True)

def install_apk(file_path):
    """安装apk文件"""
    print(install)
    return subprocess.call(["adb", "install", "-rtd", file_path], shell=True)
    
def read_manifest(manifest_path):
    with open(manifest_path, "r", encoding="utf8") as f:
        data = f.read()
    return json.loads(data)

def install_xapk(file_path):
    """安装xapk文件"""
    os.chdir(file_path)
    manifest = read_manifest("manifest.json")
    split_apks = manifest["split_apks"]
    
    if Device().sdk < int(manifest["min_sdk_version"]):
        print("安卓版本过低！")
        return None, 0
    
    if Device().sdk > int(manifest["target_sdk_version"]):
        print("安卓版本过高！")
        return None, 0
    
    install = ["adb", "install-multiple", "-rtd"]
    other_language = ["config.ar", "config.de", "config.en", "config.es", "config.fr", 
        "config.hi", "config.in", "config.it", "config.ja", "config.ko",
        "config.my", "config.pt", "config.ru", "config.th", "config.tr", 
        "config.vi", "config.zh"]
    other = ["extra_icu", "feedv2", "vr"]  # Google Chrome
    
    config = {}
    for i in split_apks:
        if i["id"]==f"config.{Device().abi.replace('-', '_')}": config["abi"] = i["file"]
        elif i["id"]==f"config.{Device().locale.split('-')[0]}": config["locale"] = i["file"]
        elif i["id"]=="config.arm64_v8a": config["arm64-v8a"] = i["file"]
        elif i["id"]=="config.armeabi_v7a": config["armeabi-v7a"] = i["file"]
        elif i["id"]=="config.xhdpi": config["xhdpi"] = i["file"]
        elif i["id"]=="config.xxhdpi": config["xxhdpi"] = i["file"]
        elif i["id"]=="config.xxxhdpi": config["xxxhdpi"] = i["file"]
        elif i["id"]=="config.tvdpi": config["tvdpi"] = i["file"]
        elif i["id"] in other_language: pass
        elif i["id"] in other: pass
        else: install.append(i["file"])
    
    if not config.get("abi"):
        for i in Device().abilist:
            if config.get(i):
                install.append(config[i])
                break
    for i in ["xhdpi", "xxhdpi", "xxxhdpi", "tvdpi"]:
        if config.get(i):
            install.append(config[i])
            break
    if config.get("locale"): install.append(config["locale"])
    
    print(install)
    return install, subprocess.call(install, shell=True)


if __name__ == "__main__":
    app = sys.argv[1]
    try:
        input("1.确保手机已经连接电脑(USB调试/无线调试)\n\r2.确保只有一个设备连接到电脑\n\r回车继续...")
        if app.endswith(".apk"):
            install_apk(app)
        elif app.endswith(".xapk"):
            unzip_path = unpack(app)
            install_xapk(unzip_path)
        elif os.path.isdir(app):
            install, status = install_xapk(app)
            if status:
                if input("安装失败！将尝试卸载后再安装，会导致数据丢失！是否继续？(yes/no)").lower()=="yes":
                    uninstall_xapk(app)
                    print(install)
                    subprocess.call(install, shell=True)
                else:
                    print("安装已取消！")
        elif app.endswith(".apks"):
            print("apks因为没有遇到过，暂时没有适配，请提供文件进行适配！")
        else:
            print(f"{app!r}不是`apk/xapk`安装包或者`xapk`安装包的解压路径！")
    except Exception as err:
        exc_type, exc_value, exc_obj = sys.exc_info()
        traceback.print_tb(exc_obj)
        print(f"{err!r}")
    os.system("pause")
