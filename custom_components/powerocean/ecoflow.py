"""ecoflow.py: API for PowerOcean integration."""
# modification of niltrip's version to provide for Power Ocean Dual Master/Slave Inverter Installations
# Andy Bowden Dec 2024 

import requests
import base64
import re
from collections import namedtuple
from requests.exceptions import RequestException

from homeassistant.exceptions import IntegrationError
from homeassistant.util.json import json_loads

from .const import _LOGGER, ISSUE_URL_ERROR_MESSAGE


# Better storage of PowerOcean endpoint
PowerOceanEndPoint = namedtuple(
    "PowerOceanEndPoint",
    "internal_unique_id, serial, name, friendly_name, value, unit, description, icon",
)


# ecoflow_api to detect device and get device info, fetch the actual data from the PowerOcean device, and parse it
# Rename, there is an official API since june
class Ecoflow:
    """Class representing Ecoflow"""

    def __init__(self, serialnumber, username, password):
        self.sn = serialnumber
        self.unique_id = serialnumber
        self.ecoflow_username = username
        self.ecoflow_password = password
        self.token = None
        self.device = None
        self.session = requests.Session()
        self.url_iot_app = "https://api.ecoflow.com/auth/login"
        self.url_user_fetch = f"https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}"
        # self.authorize()  # authorize user and get device details

    def get_device(self):
        """Function get device"""
        self.device = {
            "product": "PowerOcean",
            "vendor": "Ecoflow",
            "serial": self.sn,
            "version": "5.1.15",  # TODO: woher bekommt man diese Info?
            "build": "6",  # TODO: wo finde ich das?
            "name": "PowerOcean",
            "features": "Photovoltaik",
        }

        return self.device

    def authorize(self):
        """Function authorize"""
        auth_ok = False  # default
        headers = {"lang": "en_US", "content-type": "application/json"}
        data = {
            "email": self.ecoflow_username,
            "password": base64.b64encode(self.ecoflow_password.encode()).decode(),
            "scene": "IOT_APP",
            "userType": "ECOFLOW",
        }

        try:
            url = self.url_iot_app
            _LOGGER.info("Login to EcoFlow API %s", {url})
            request = requests.post(url, json=data, headers=headers)
            response = self.get_json_response(request)

        except ConnectionError:
            error = f"Unable to connect to {self.url_iot_app}. Device might be offline."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

        try:
            self.token = response["data"]["token"]
            self.user_id = response["data"]["user"]["userId"]
            user_name = response["data"]["user"].get("name", "<no user name>")
            auth_ok = True
        except KeyError as key:
            raise Exception(f"Failed to extract key {key} from response: {response}")

        _LOGGER.info("Successfully logged in: %s", {user_name})

        self.get_device()  # collect device info

        return auth_ok

    def get_json_response(self, request):
        """Function get json response"""
        if request.status_code != 200:
            raise Exception(
                f"Got HTTP status code {request.status_code}: {request.text}"
            )
        try:
            response = json_loads(request.text)
            response_message = response["message"]
        except KeyError as key:
            raise Exception(
                f"Failed to extract key {key} from {json_loads(request.text)}"
            )
        except Exception as error:
            raise Exception(f"Failed to parse response: {request.text} Error: {error}")

        if response_message.lower() != "success":
            raise Exception(f"{response_message}")

        return response

    # Fetch the data from the PowerOcean device, which then constitues the Sensors
    def fetch_data(self):
        """Function fetch data from Url."""
        # curl 'https://api-e.ecoflow.com/provider-service/user/device/detail?sn={self.sn}}' \
        # -H 'authorization: Bearer {self.token}'

        url = self.url_user_fetch
        try:
            headers = {"authorization": f"Bearer {self.token}"}
            request = requests.get(self.url_user_fetch, headers=headers, timeout=30)
            response = self.get_json_response(request)

            _LOGGER.debug(f"{response}")

            return self._get_sensors(response)

        except ConnectionError:
            error = f"ConnectionError in fetch_data: Unable to connect to {url}. Device might be offline."
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

        except RequestException as e:
            error = f"RequestException in fetch_data: Error while fetching data from {url}: {e}"
            _LOGGER.warning(error + ISSUE_URL_ERROR_MESSAGE)
            raise IntegrationError(error)

    def __get_unit(self, key):
        """Function get unit from key Name."""
        if key.endswith(("pwr", "Pwr", "Power")):
            unit = "W"
        elif key.endswith(("amp", "Amp")):
            unit = "A"
        elif key.endswith(("soc", "Soc", "soh", "Soh")):
            unit = "%"
        elif key.endswith(("vol", "Vol")):
            unit = "V"
        elif key.endswith(("Watth", "Energy")):
            unit = "Wh"
        elif "Generation" in key:
            unit = "kWh"
        elif key.startswith("bpTemp"):  # TODO: alternative: 'Temp' in key
            unit = "°C"
        else:
            unit = None

        return unit

    def __get_description(self, key):
        # TODO: hier könnte man noch mehr definieren bzw ein translation dict erstellen +1
        # Comment: Ich glaube hier brauchen wir n
        description = key  # default description
        if key == "sysLoadPwr":
            description = "Hausnetz"
        if key == "sysGridPwr":
            description = "Stromnetz"
        if key == "mpptPwr":
            description = "Solarertrag"
        if key == "bpPwr":
            description = "Batterieleistung"
        if key == "bpSoc":
            description = "Ladezustand der Batterie"
        if key == "online":
            description = "Online"
        if key == "systemName":
            description = "System Name"
        if key == "createTime":
            description = "Installations Datum"
        # Battery descriptions
        if key == "bpVol":
            description = "Batteriespannung"
        if key == "bpAmp":
            description = "Batteriestrom"
        if key == "bpCycles":
            description = "Ladezyklen"
        if key == "bpTemp":
            description = "Temperatur der Batteriezellen"

        return description

    def _get_sensors(self, response):
        # check if power ocean system is a dual master slave installation
        
        serials = self._get_serial_numbers(response)
        _LOGGER.debug(f"no_serials_found__{serials}")

        if serials == 2:
            serial_copy = serials
            _LOGGER.debug(f"dual inverter system")
            _LOGGER.debug(f"master_found__{self.master_sn}")
            _LOGGER.debug(f"slave_found__{self.slave_sn}")
            master_string = "_master"
            slave_string = "_slave"
        elif serials == 0:
            _LOGGER.debug(f"single inverter system")
            master_string = ""
        else:
            _LOGGER.debug(f"neither single nor dual inverter system")
            
        # get system sensors from response['data']
        
        sensors = self.__get_sensors_data(response)
        
        _LOGGER.debug(f"system_sensors_found__{list(sensors)}")

        # get sensors from master 'JTS1_ENERGY_STREAM_REPORT'
        # sensors = self.__get_sensors_energy_stream(self.master_data, sensors)  # is currently not in use

        # get sensors from master 'JTS1_EMS_CHANGE_REPORT'
        # siehe parameter_selected.json    #  get bpSoc from ems_change
        
        sensors = self.__get_sensors_ems_change(self.master_data, sensors, self.master_sn, master_string)

        _LOGGER.debug(f"change_sensors_found__{sensors}")
        _LOGGER.debug(f"change_sensors_found__{list(sensors)}")

        # get info from master batteries  => JTS1_BP_STA_REPORT
        sensors = self.__get_sensors_battery(self.master_data, sensors, self.master_sn, master_string)
        
        _LOGGER.debug(f"battery_sensors_found__{sensors}")
        _LOGGER.debug(f"battery_sensors_found__{list(sensors)}")

        # get info from master PV strings  => JTS1_EMS_HEARTBEAT
        sensors = self.__get_sensors_ems_heartbeat(self.master_data, sensors, self.master_sn, master_string)
        
        _LOGGER.debug(f"full_sensors__{sensors}")
        _LOGGER.debug(f"full_sensors__{list(sensors)}")

        if serials == 2:
            # get sensors from master 'JTS1_ENERGY_STREAM_REPORT'
            # sensors = self.__get_sensors_energy_stream(self.slave_data, sensors, self.slave_sn, slave_string)  # is currently not in use

            # get sensors from slave 'JTS1_EMS_CHANGE_REPORT'
            # siehe parameter_selected.json    #  get bpSoc from ems_change
        
            sensors = self.__get_sensors_ems_change(self.slave_data, sensors, self.slave_sn, slave_string)

            _LOGGER.debug(f"change_sensors_found__{sensors}")
            _LOGGER.debug(f"change_sensors_found__{list(sensors)}")

            # get info from slave batteries  => JTS1_BP_STA_REPORT
            sensors = self.__get_sensors_battery(self.slave_data, sensors, self.slave_sn, slave_string)
        
            _LOGGER.debug(f"battery_sensors_found__{sensors}")
            _LOGGER.debug(f"battery_sensors_found__{list(sensors)}")

            # get info from slave PV strings  => JTS1_EMS_HEARTBEAT
            sensors = self.__get_sensors_ems_heartbeat(self.slave_data, sensors, self.slave_sn, slave_string)
        
            _LOGGER.debug(f"full_sensors__{sensors}")
            _LOGGER.debug(f"full_sensors__{list(sensors)}")
        

        return sensors

    def __get_sensors_data(self, response):
        d = response["data"].copy()

        # sensors not in use: note, bpSoc is taken from the EMS CHANGE report
        # [ 'bpSoc', 'sysBatChgUpLimit', 'sysBatDsgDownLimit','sysGridSta', 'sysOnOffMachineStat',
        #   'location', 'timezone', 'quota']

        sens_select = [
            "sysLoadPwr",
            "sysGridPwr",
            "mpptPwr",
            "bpPwr",
            "online",
            "todayElectricityGeneration",
            "monthElectricityGeneration",
            "yearElectricityGeneration",
            "totalElectricityGeneration",
            "systemName",
            "createTime",
        ]

        sensors = dict()  # start with empty dict
        for key, value in d.items():
            if key in sens_select:  # use only sensors in sens_select
                if not isinstance(value, dict):
                    # default uid, unit and descript
                    unique_id = f"{self.sn}_{key}"
                    special_icon = None
                    if key == "mpptPwr":
                        special_icon = "mdi:solar-power"

                    sensors[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=self.sn,
                        name=f"{self.sn}_{key}",
                        friendly_name=key,
                        value=value,
                        unit=self.__get_unit(key),
                        description=self.__get_description(key),
                        icon=special_icon,
                    )

        return sensors

    # Note, this report is currently not in use. Sensors are taken from response['data']
    # def __get_sensors_energy_stream(self, response, sensors):
    #     report = "JTS1_ENERGY_STREAM_REPORT"
    #     d = response["data"]["quota"][report]
    #     prefix = (
    #         "_".join(report.split("_")[1:3]).lower() + "_"
    #     )  # used to construct sensor name
    #
    #     # sens_all = ['bpSoc', 'mpptPwr', 'updateTime', 'bpPwr', 'sysLoadPwr', 'sysGridPwr']
    #     sens_select = d.keys()
    #     data = {}
    #     for key, value in d.items():
    #         if key in sens_select:  # use only sensors in sens_select
    #             # default uid, unit and descript
    #             unique_id = f"{self.sn}_{report}_{key}"
    #
    #             data[unique_id] = PowerOceanEndPoint(
    #                 internal_unique_id=unique_id,
    #                 serial=self.sn,
    #                 name=f"{self.sn}_{prefix+key}",
    #                 friendly_name=prefix + key,
    #                 value=value,
    #                 unit=self.__get_unit(key),
    #                 description=self.__get_description(key),
    #                 icon=None,
    #             )
    #     dict.update(sensors, data)
    #
    #     return sensors

    def __get_sensors_ems_change(self, inverter_dataset, sensors, inverter_sn, inverter_string):
        report = "JTS1_EMS_CHANGE_REPORT"
        d = inverter_dataset[report]

        _LOGGER.debug(f"inverter_change_subset__{d}")


        sens_select = [
            "bpTotalChgEnergy",
            "bpTotalDsgEnergy",
            "bpSoc",
            "bpOnlineSum",  # number of batteries
            "emsCtrlLedBright",
        ]

        # add mppt Warning/Fault Codes
        keys = d.keys()

        _LOGGER.debug(f"inverter_change_subset_keys__{keys}")
        
        r = re.compile("mppt.*Code")
        wfc = list(filter(r.match, keys))  # warning/fault code keys
        sens_select += wfc

        data = {}
        for key, value in d.items():
            if key in sens_select:  # use only sensors in sens_select
                # default uid, unit and descript
                unique_id = f"{inverter_sn}_{report}_{key}"

                _LOGGER.debug(f"inverter_change_subset_key_found__{key}")


                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=inverter_sn,
                    name=f"{inverter_sn}_{key}",
                    friendly_name=key + inverter_string,
                    value=value,
                    unit=self.__get_unit(key),
                    description=self.__get_description(key),
                    icon=None,
                )
        _LOGGER.debug(f"inverter_change_additions__{data}")
        _LOGGER.debug(f"sensors_before__{list(sensors)}")
        dict.update(sensors, data)
        _LOGGER.debug(f"sensors_after__{list(sensors)}")
        
        return sensors

    def __get_sensors_battery(self, inverter_data, sensors, inverter_sn, inverter_string):
        report = "JTS1_BP_STA_REPORT"
        # change to process inverter data set
        
        d = inverter_data[report]
        
        _LOGGER.debug(f"inverter_battery_subset__{d}")

        keys = list(d.keys())

        # loop over N batteries:
        batts = [s for s in keys if len(s) > 12]
        bat_sens_select = [
            "bpPwr",
            "bpSoc",
            "bpSoh",
            "bpVol",
            "bpAmp",
            "bpCycles",
            "bpSysState",
            "bpRemainWatth",
        ]

        data = {}
        prefix = "bpack"
        for ibat, bat in enumerate(batts):
            name = prefix + "%i_" % (ibat + 1)
            d_bat = json_loads(d[bat])
            for key, value in d_bat.items():
                if key in bat_sens_select:
                    # default uid, unit and descript
                    unique_id = f"{inverter_sn}_{report}_{bat}_{key}"
                    description_tmp = f"{name}" + self.__get_description(key)
                    special_icon = None
                    if key == "bpAmp":
                        special_icon = "mdi:current-dc"
                    data[unique_id] = PowerOceanEndPoint(
                        internal_unique_id=unique_id,
                        serial=inverter_sn,
                        name=f"{inverter_sn}_{name + key}",
                        friendly_name=name + key + inverter_string,
                        value=value,
                        unit=self.__get_unit(key),
                        description=description_tmp,
                        icon=special_icon,
                    )
            # compute mean temperature of cells
            key = "bpTemp"
            temp = d_bat[key]
            value = sum(temp) / len(temp)
            unique_id = f"{inverter_sn}_{report}_{bat}_{key}"
            description_tmp = f"{name}" + self.__get_description(key)
            data[unique_id] = PowerOceanEndPoint(
                internal_unique_id=unique_id,
                serial=inverter_sn,
                name=f"{inverter_sn}_{name + key}",
                friendly_name=name + key + inverter_string,
                value=value,
                unit=self.__get_unit(key),
                description=description_tmp,
                icon=None,
            )

        dict.update(sensors, data)

        return sensors

    def __get_sensors_ems_heartbeat(self, inverter_data, sensors, inverter_sn, inverter_string):
        report = "JTS1_EMS_HEARTBEAT"
        d = inverter_data[report]

        _LOGGER.debug(f"inverter_heartbeat_subset__{d}")

        # sens_select = d.keys()  # 68 Felder
        sens_select = [
            "bpRemainWatth",
            "emsBpAliveNum",
            "emsBpPower",
            "pcsActPwr",
            "pcsMeterPower",

        ]
        data = {}
        for key, value in d.items():
            if key in sens_select:
                # default uid, unit and descript
                unique_id = f"{inverter_sn}_{report}_{key}"
                description_tmp = self.__get_description(key)
                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=inverter_sn,
                    name=f"{inverter_sn}_{key}",
                    friendly_name=key + inverter_string,
                    value=value,
                    unit=self.__get_unit(key),
                    description=description_tmp,
                    icon=None,
                )

        # special for phases
        phases = ["pcsAPhase", "pcsBPhase", "pcsCPhase"]
        for i, phase in enumerate(phases):
            for key, value in d[phase].items():
                name = phase + "_" + key
                unique_id = f"{inverter_sn}_{report}_{name}"

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=inverter_sn,
                    name=f"{inverter_sn}_{name}",
                    friendly_name=f"{name}{inverter_string}",
                    value=value,
                    unit=self.__get_unit(key),
                    description=self.__get_description(key),
                    icon=None,
                )

        # special for mpptPv
        n_strings = len(d["mpptHeartBeat"][0]["mpptPv"])  # TODO: auch als Sensor?
        mpptpvs = []
        for i in range(1, n_strings + 1):
            mpptpvs.append(f"mpptPv{i}")
        mpptPv_sum = 0.0
        for i, mpptpv in enumerate(mpptpvs):
            for key, value in d["mpptHeartBeat"][0]["mpptPv"][i].items():
                unique_id = f"{inverter_sn}_{report}_mpptHeartBeat_{mpptpv}_{key}"
                special_icon = None
                if key.endswith("amp"):
                    special_icon = "mdi:current-dc"
                if key.endswith("pwr"):
                    special_icon = "mdi:solar-power"

                data[unique_id] = PowerOceanEndPoint(
                    internal_unique_id=unique_id,
                    serial=self.sn,
                    name=f"{inverter_sn}_{mpptpv}_{key}",
                    friendly_name=f"{mpptpv}_{key}{inverter_string}",
                    value=value,
                    unit=self.__get_unit(key),
                    description=self.__get_description(key),
                    icon=special_icon,
                )
                # sum power of all strings
                if key == "pwr":
                    mpptPv_sum += value

        # create total power sensor of all strings
        name = "mpptPv_pwrTotal"
        unique_id = f"{inverter_sn}_{report}_mpptHeartBeat_{name}"

        data[unique_id] = PowerOceanEndPoint(
            internal_unique_id=unique_id,
            serial=inverter_sn,
            name=f"{inverter_sn}_{name}",
            friendly_name=f"{name}{inverter_string}",
            value=mpptPv_sum,
            unit=self.__get_unit(key),
            description="Solarertrag aller Strings",
            icon="mdi:solar-power",
        )

        dict.update(sensors, data)

        return sensors
        
    def _get_serial_numbers(self, response):
      
        p = response["data"]
        _LOGGER.debug(f"reponse__{p}")
        _LOGGER.debug(f"keys_present__{p.keys()}")
        _LOGGER.debug(f"keys_type__{type(p.keys())}")

        if 'parallel' in p.keys():
            _LOGGER.debug(f"parallel_found")
            
        
            p = response["data"]["parallel"]

        else:
            self.master_data = response["data"]["quota"]
            return 0
        
 
        keys_2 = p.keys()
        _LOGGER.debug(f"serial_p_keys2__{keys_2}")
    
        for key in p.keys():
            pp = response["data"]["parallel"][key]
            keys_3 = pp.keys()
            _LOGGER.debug(f"serial_pp_keys___{keys_3}")

        self.slave_sn = next(iter(keys_2))
        self.master_sn = next(reversed(keys_2))

        self.master_data = response["data"]["parallel"][self.master_sn]
        self.slave_data = response["data"]["parallel"][self.slave_sn]
        
        return len(p)
  




class AuthenticationFailed(Exception):
    """Exception to indicate authentication failure."""
