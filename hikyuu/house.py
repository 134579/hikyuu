#!/usr/bin/env python
# -*- coding: utf8 -*-
# cp936
#
#===============================================================================
# History
# 1. 20200816, Added by fasiondog
#===============================================================================

import os
import sys
import shutil
import logging
import importlib
import git
from configparser import ConfigParser

from hikyuu.util.check import checkif
from hikyuu.util.singleton import SingletonType

from sqlalchemy import (create_engine, Sequence, Column, Integer, String, and_, UniqueConstraint)
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()


class ConfigModel(Base):
    __tablename__ = 'house_config'
    id = Column(Integer, Sequence('config_id_seq'), primary_key=True)
    key = Column(String, index=True)  # 参数名
    value = Column(String)  # 参数值

    __table_args__ = (UniqueConstraint('key'), )

    def __str__(self):
        return "ConfigModel(id={}, key={}, value={})".format(self.id, self.key, self.value)

    def __repr__(self):
        return "<{}>".format(self.__str__())


class HouseModel(Base):
    __tablename__ = 'house_repo'
    id = Column(Integer, Sequence('remote_id_seq'), primary_key=True)
    name = Column(String, index=True)  # 本地仓库名
    house_type = Column(String)  # 'remote' (远程仓库) | 'local' （本地仓库）
    local = Column(String)  # 本地地址
    url = Column(String)  # git 仓库地址
    branch = Column(String)  # 远程仓库分支

    __table_args__ = (UniqueConstraint('name'), )

    def __str__(self):
        return "HouseModel(id={}, name={}, house_type={}, local={}, url={}, branch={})".format(
            self.id, self.name, self.house_type, self.local, self.url, self.branch
        )

    def __repr__(self):
        return "<{}>".format(self.__str__())


class PartModel(Base):
    __tablename__ = 'house_part'
    id = Column(Integer, Sequence('part_id_seq'), primary_key=True)
    house_name = Column(String)  #所属仓库标识
    part = Column(String)  # 部件类型
    module_name = Column(String)  # 实际策略导入模块名
    name = Column(String)  # 策略名称
    author = Column(String)  # 策略作者
    brief = Column(String)  # 策略概要描述
    params = Column(String)  # 策略参数说明

    def __str__(self):
        return 'PartModel(id={}, house_name={}, part={}, name={}, author={}, module_name={})'.format(
            self.id, self.house_name, self.part, self.name, self.author, self.module_name
        )

    def __repr__(self):
        return '<{}>'.format(self.__str__())


class HouseNameRepeatError(Exception):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return "已存在相同名称的仓库（{}），请更换仓库名！".format(self.name)


class ModuleConflictError(Exception):
    def __init__(self, house_name, conflict_module, house_path):
        self.house_name = house_name
        self.conflict_module = conflict_module
        self.house_path = house_path

    def __str__(self):
        return '仓库名（{}）与其他 python 模块（"{}"）冲突，请更改仓库目录名称！（"{}"）'.format(
            self.house_name, self.conflict_module, self.house_path
        )


class PartNotFoundError(Exception):
    def __init__(self, name, cause):
        self.name = name
        self.cause = cause

    def __str__(self):
        return '未找到指定的策略部件: "{}", {}!'.format(self.name, self.cause)


def dbsession(func):
    def wrapfunc(*args, **kwargs):
        x = args[0]
        old_session = x._session
        if x._session is None:
            x._session = x._scoped_Session()
        result = func(*args, **kwargs)
        x._session.commit()
        if old_session is not x._session:
            x._session.close()
            x._session = old_session
        return result

    return wrapfunc


class HouseManager(metaclass=SingletonType):
    """策略库管理"""
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        usr_dir = os.path.expanduser('~')

        # 创建仓库数据库
        engine = create_engine("sqlite:///{}/.hikyuu/stockhouse.db".format(usr_dir))
        Base.metadata.create_all(engine)
        self._scoped_Session = scoped_session(
            sessionmaker(autocommit=False, autoflush=False, bind=engine)
        )
        self._session = None

    @dbsession
    def setup_house(self):
        """初始化 hikyuu 默认策略仓库"""
        usr_dir = os.path.expanduser('~')

        # 检查并建立远端仓库的本地缓存目录
        self.remote_cache_dir = self._session.query(ConfigModel.value
                                                    ).filter(ConfigModel.key == 'remote_cache_dir'
                                                             ).first()
        if self.remote_cache_dir is None:
            self.remote_cache_dir = "{}/.hikyuu/house_cache".format(usr_dir)
            record = ConfigModel(key='remote_cache_dir', value=self.remote_cache_dir)
            self._session.add(record)
        else:
            self.remote_cache_dir = self.remote_cache_dir[0]

        if not os.path.lexists(self.remote_cache_dir):
            os.makedirs(self.remote_cache_dir)

        # 将远程仓库本地缓存地址加入系统路径
        sys.path.append(self.remote_cache_dir)

        # 将所有本地仓库的上层路径加入系统路径
        house_models = self._session.query(HouseModel).filter_by(house_type='local').all()
        for model in house_models:
            sys.path.append(os.path.dirname(model.local))

        # 检查并下载 hikyuu 默认策略仓库, hikyuu_house 避免导入时模块和 hikyuu 重名
        hikyuu_house_path = self._session.query(HouseModel.local
                                                ).filter(HouseModel.name == 'default').first()
        if hikyuu_house_path is None:
            self.add_remote_house(
                'default', 'https://gitee.com/fasiondog/hikyuu_house.git', 'master'
            )

    def download_remote_house(self, local_dir, url, branch):
        print('正在下载 hikyuu 策略仓库至："{}"'.format(local_dir))

        # 如果存在同名缓存目录，则强制删除
        if os.path.lexists(local_dir):
            shutil.rmtree(local_dir)

        try:
            clone = git.Repo.clone_from(url, local_dir, branch=branch)
        except:
            raise RuntimeError("请检查网络是否正常或链接地址({})是否正确!".format(url))
        print('下载完毕')

    @dbsession
    def add_remote_house(self, name, url, branch='master'):
        """增加远程策略仓库

        :param str name: 本地仓库名称（自行起名）
        :param str url: git 仓库地址
        :param str branch: git 仓库分支
        """
        record = self._session.query(HouseModel).filter(HouseModel.name == name).first()
        checkif(record is not None, name, HouseNameRepeatError)

        record = self._session.query(HouseModel).filter(
            and_(HouseModel.url == url, HouseModel.branch == branch)
        ).first()

        # 下载远程仓库
        local_dir = "{}/{}".format(self.remote_cache_dir, name)
        self.download_remote_house(local_dir, url, branch)

        # 导入仓库各部件策略信息
        record = HouseModel(name=name, house_type='remote', url=url, branch=branch, local=local_dir)
        self.import_part_to_db(record)

        # 更新仓库记录
        self._session.add(record)

    @dbsession
    def add_local_house(self, path):
        """增加本地数据仓库

        :param str path: 本地全路径
        """
        checkif(not os.path.lexists(path), '找不到指定的路径（"{}"）'.format(path))

        # 获取绝对路径
        local_path = os.path.abspath(path)
        name = os.path.basename(local_path)

        record = self._session.query(HouseModel).filter(HouseModel.name == name).first()
        checkif(record is not None, name, HouseNameRepeatError)
        #assert record is None, '本地仓库名重复'

        # 将本地路径的上一层路径加入系统路径
        sys.path.append(os.path.dirname(path))

        # 检查仓库目录名称是否与其他 python 模块存在冲突
        tmp = importlib.import_module(name)
        checkif(
            tmp.__path__[0] != local_path,
            name,
            ModuleConflictError,
            conflict_module=tmp.__path__[0],
            house_path=local_path
        )

        # 导入部件信息
        house_model = HouseModel(name=name, house_type='local', local=local_path)
        self.import_part_to_db(house_model)

        # 更新仓库记录
        self._session.add(house_model)

    @dbsession
    def update_house(self, name):
        """更新指定仓库

        :param str name: 仓库名称
        """
        house_model = self._session.query(HouseModel).filter_by(name=name).first()
        checkif(house_model is None, '指定的仓库（{}）不存在！'.format(name))

        self._session.query(PartModel).filter_by(house_name=name).delete()
        if house_model.house_type == 'remote':
            self.download_remote_house(house_model.local, house_model.url, house_model.branch)
        self.import_part_to_db(house_model)

    @dbsession
    def remove_house(self, name):
        """删除指定的仓库

        :param str name: 仓库名称
        """
        self._session.query(PartModel).filter_by(house_name=name).delete()
        self._session.query(HouseModel).filter_by(name=name).delete()

    @dbsession
    def import_part_to_db(self, house_model):
        part_dict = {
            'af': 'part/af',
            'cn': 'part/cn',
            'ev': 'part/ev',
            'mm': 'part/mm',
            'pg': 'part/pg',
            'se': 'part/se',
            'sg': 'part/sg',
            'sp': 'part/sp',
            'st': 'part/st',
            'portfolio': 'portfolio',
            'system': 'system',
        }

        # 检查仓库本地目录是否存在，不存在则给出告警信息并直接返回
        local_dir = house_model.local
        if not os.path.lexists(local_dir):
            self.logger.warning(
                'The {} house path ("{}") is not exists! Ignored this house!'.format(
                    house_model.name, house_model.local
                )
            )
            return

        base_local = os.path.basename(local_dir)

        # 遍历仓库导入部件信息
        for part, part_dir in part_dict.items():
            path = "{}/{}".format(house_model.local, part_dir)
            try:
                with os.scandir(path) as it:
                    for entry in it:
                        if (not entry.name.startswith('.')
                            ) and entry.is_dir() and (entry.name != "__pycache__"):
                            # 计算实际的导入模块名
                            module_name = '{}.part.{}.{}.part'.format(
                                base_local, part, entry.name
                            ) if part not in (
                                'portfolio', 'system'
                            ) else '{}.{}.{}.part'.format(base_local, part, entry.name)

                            # 导入模块
                            try:
                                part_module = importlib.import_module(module_name)
                            except ModuleNotFoundError:
                                print(module_name)
                                self.logger.error('忽略：缺失 part.py 文件, 位置："{}"！'.format(entry.path))
                                continue

                            module_vars = vars(part_module)

                            name = '{}.{}.{}'.format(
                                house_model.name, part, entry.name
                            ) if part not in (
                                'portfolio', 'system'
                            ) else '{}.{}.{}'.format(house_model.name, part, entry.name)

                            part_model = PartModel(
                                house_name=house_model.name,
                                part=part,
                                name=name,
                                module_name=module_name,
                                author=part_module.author if 'author' in module_vars else 'None',
                                brief=part_module.brief if 'brief' in module_vars else 'None',
                                params=str(part_module.params)
                                if 'params' in module_vars else 'None'
                            )
                            self._session.add(part_model)
                            #print(part_model)
            except FileNotFoundError:
                continue

    @dbsession
    def get_part(self, name):
        """获取指定策略部件

        :param str name: 策略部件名称
        """
        part_model = self._session.query(PartModel).filter_by(name=name).first()
        checkif(part_model is None, name, PartNotFoundError, cause='仓库中不存在')
        try:
            part_module = importlib.import_module(part_model.module_name)
        except ModuleNotFoundError:
            raise PartNotFoundError(name, '请检查部件对应路径是否存在')
        part = part_module.sg.clone()
        part.name = part_model.name
        return part


def add_remote_house(name, url, branch='master'):
    """增加远程策略仓库

    :param str name: 本地仓库名称（自行起名）
    :param str url: git 仓库地址
    :param str branch: git 仓库分支
    """
    HouseManager().add_remote_house()


def add_local_house(path):
    """增加本地数据仓库

    :param str path: 本地全路径
    """
    HouseManager().add_local_house(path)


def update_house(name):
    """更新指定仓库

    :param str name: 仓库名称
    """
    HouseManager().update_house(name)


def remove_house(name):
    """删除指定的仓库

    :param str name: 仓库名称
    """
    HouseManager().remove_house(name)


def get_part(name):
    """获取指定策略部件

    :param str name: 策略部件名称
    """
    return HouseManager().get_part(name)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)-15s [%(levelname)s] - %(message)s [%(name)s::%(funcName)s]'
    )
    house = HouseManager()
    house.setup_house()
    #add_local_house('/home/fasiondog/workspace/test1')
    #update_house('test1')
    #update_house('default')
    #remove_house('test1')
    remove_house('test')
    sg = house.get_part('default.sg.ama')
    print(sg)
    sg = get_part('default.sg.ama')
    print(sg)

    #house.get_part('hikyuu_house.sg.tt')