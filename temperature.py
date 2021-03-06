#!/usr/bin/env python
# -*- coding: utf-8 -*-

import mysql.connector
import ConfigParser
import datetime
import time
import os, sys
from tinkerforge.ip_connection import IPConnection
from tinkerforge.bricklet_temperature import Temperature
from tinkerforge.bricklet_dual_relay import DualRelay
from tinkerforge.bricklet_ptc import PTC
from tinkerforge.bricklet_rs232 import BrickletRS232

class EthTemperature:
	HOST = "192.168.3.150"
	PORT = 4223
	DB_NAME = "tl"
	DB_HOST = "127.0.0.1"
	DB_USER = "DBUSER"
	DB_PASS = "DBPASS"
	TB_OFFSET = 0 * 100
	PTC_OFFSET = 2.7 * 100
	CISTERN_MAX = 1000
	CISTERN_MIN = 2500


	def __init__(self):
		self.temp 	= None
		self.ptc 	= None
		self.rs232	= None
		self.dr 	= None
		self.relay	= 2

		self.ipcon 	= None
		self.ready 	= 0
		self.config = {}

		self.cursor = None
		self.cnx 	= None

		self.now = datetime.datetime.now()
		self.file = self.now.strftime('%Y-%m-%d')

		self.connect_db()
		self.read_config()

		# create ip connection
		self.ipcon = IPConnection()

		# register ip connection callbacks
		self.ipcon.register_callback(IPConnection.CALLBACK_ENUMERATE,
						self.cb_enumerate)

		self.ipcon.register_callback(IPConnection.CALLBACK_CONNECTED,
						self.cb_connected)

		try:
			self.ipcon.connect(EthTemperature.HOST, EthTemperature.PORT)
		except:
			print "Could not connect to tinkerforge"
			self.ipcon = None


	def release(self):
		if self.ipcon is not None:
			self.ipcon.disconnect()

		if self.cursor is not None:
			self.cursor.close()

		if self.cnx is not None:
			self.cnx.close()


	def connect_db(self):
		'''
			Connect to database
		'''
		try:
			self.cnx = mysql.connector.connect(user=EthTemperature.DB_USER, password=EthTemperature.DB_PASS,
				host=EthTemperature.DB_HOST, database=EthTemperature.DB_NAME)

			self.cursor = self.cnx.cursor()
		except:
			print "Could not connect to database!"
			self.cnx = None
			self.cursor = None


	def read_config(self):
		'''
			Read config parameters from database
		'''
		if self.cursor is None:
			return

		sql = ("SELECT cfg_key, cfg_value FROM tl_config")
		self.cursor.execute(sql)

		for (cfg_key, cfg_value) in self.cursor:
			self.config[cfg_key] = cfg_value


	def get_temperature(self, sensor):
		'''
			Get temperature from selected sensor
		'''
		if sensor == "PTC":
			if self.ptc is None:
				return 0
			else:
				return self.ptc.get_temperature() + EthTemperature.PTC_OFFSET
		elif sensor == "TB":
			if self.temp is None:
				return 0
			else:
				return self.temp.get_temperature() + EthTemperature.TB_OFFSET


	def _flush_cistern_sensor(self):
		msg_len = 1
		while msg_len != 0:
			us_msg = self.rs232.read()
			msg_len = us_msg[1]
		time.sleep(0.5)
		return


	def get_cistern_level(self):
		'''
			Read the ultrasonic sensor from cistern
		'''
		# check if bricklet connected
		if self.rs232 == None:
			return 0

		dist = 0

		us_msg = self.rs232.read()

		if us_msg[1] > 0:
			# get message
			us_msg = us_msg[0]

			# search value in message tuple
			start = None
			i = 0
			for c in us_msg:
				if c == 'R':
					start = i+1
				if start is not None and c == '\r':
					end = i
					break
				i = i + 1

			if start is not None:
				dist = ''.join(us_msg[start:end]) # in mm
				dist = int(dist)

		return dist


	def calc_cistern_water_volume(self, dist):
		raise Exception("Not implemented!")


	def log_cistern_level(self):
		'''
			Log cistern level into database
		'''
		if self.cursor == None or self.cnx == None:
			return

		dist = get_cistern_level()

		# check if value is valid
		if dist == 0 or dist == 5000:
			return

		sql = ("INSERT INTO tl_measurements_cistern "
               "(measurement_date, level) "
               "VALUES (%(a)s, %(b)s")

		args = { 'a' : self.now.strftime('%Y-%m-%d %H:%M:%S'), 'b' : dist}

		self.cursor.execute(sql,args)

		# insert new cistern level

		mid = self.cursor.lastrowid

		self.cnx.commit()


	def log_temperature(self):
		'''
			Log temperature into database
		'''
		if self.cursor == None or self.cnx == None:
			return

		temp_inside = self.get_temperature("TB")
		temp_outside = self.get_temperature("PTC")

		sql = ("INSERT INTO tl_measurements "
               "(measurement_date, temperature, temperature_ptc) "
               "VALUES (%(a)s, %(b)s, %(c)s)")

		args = { 'a' : self.now.strftime('%Y-%m-%d %H:%M:%S'), 'b' : temp_inside, 'c' : temp_outside}

		self.cursor.execute(sql,args)

		# insert new temperature

		mid = self.cursor.lastrowid

		self.cnx.commit()


	def set_relay(self,state):
		'''
			Switch the relay
		'''
		if self.dr is None or isinstance(state,bool) is False:
			return

		self.dr.set_selected_state(self.relay, state)


	def check_temperature(self):
		'''
			Check if temperature under min or over max and switch the relay.
		'''
		if self.ptc is None:
			return

		temp_inside = self.get_temperature("PTC")

		if temp_inside/100.0  < float(self.config["min_temp"]):
			self.set_relay(True)
		elif temp_inside/100.0  > float(self.config["max_temp"]):
			self.set_relay(False)


	def cb_enumerate(self, uid, connected_uid, position, hardware_version,
				firmware_version, device_identifier, enumeration_type):
		'''
			Callback handles device connections and configures possibly lost
		'''
		if enumeration_type == IPConnection.ENUMERATION_TYPE_CONNECTED or enumeration_type == IPConnection.ENUMERATION_TYPE_AVAILABLE:
			# enumeration is for temperature bricklet
			if device_identifier == Temperature.DEVICE_IDENTIFIER:
				# create temperature device object
				self.temp = Temperature(uid, self.ipcon)
				self.ready = self.ready + 1

			if device_identifier == DualRelay.DEVICE_IDENTIFIER:
				# create dual relay device object
				self.dr = DualRelay(uid, self.ipcon)
				self.ready = self.ready + 1

			if device_identifier == PTC.DEVICE_IDENTIFIER:
				# create ptc device object
				self.ptc = PTC(uid, self.ipcon)

				self.ptc.set_wire_mode(PTC.WIRE_MODE_3)

				self.ready = self.ready + 1
			if device_identifier == BrickletRS232.DEVICE_IDENTIFIER:
				# create rs232 device object
				self.rs232 = BrickletRS232(uid, self.ipcon)
				# set configuration for ultra sonic sensor
				self.rs232.set_configuration(BrickletRS232.BAUDRATE_9600, BrickletRS232.PARITY_NONE, BrickletRS232.STOPBITS_1,
					BrickletRS232.WORDLENGTH_8, BrickletRS232.HARDWARE_FLOWCONTROL_OFF, BrickletRS232.SOFTWARE_FLOWCONTROL_OFF)


	def is_ready(self, mode):
		'''
			Check if all sensors for chossen mode connected
		'''
		if mode == 'temperature':
			if self.dr is not None and self.ptc is not None:
				return True
		if mode == 'cistern':
			if self.rs232 is not None:
				return True
		return False


	def cb_connected(self, connected_reason):
		'''
			Callback handles reconnection of ip connection
		'''
		# enumerate devices again. if we reconnected, the bricks/bricklets
		# may have been offline and the configuration may be lost.
		# in this case we don't care for the reason of the connection
		self.ipcon.enumerate()


if __name__ == "__main__":
	et = EthTemperature()
	i = 0

	# check if sensors connected
	while et.is_ready('temperature') != True:
		time.sleep(0.5)
		if i == 6: # exit programm, if no sensor connection after 3 secs
			et.release()
			sys.exit(0)
		i = i + 1

	if et.is_ready('temperature'):
		if et.now.minute == 0: # log temp every hour
			et.log_temperature()
		et.check_temperature()

	if et.is_ready('cistern'):
		print et.get_cistern_level()

	et.release()

