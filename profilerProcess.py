import multiprocessing
import json
from datetime import datetime
from datetime import timedelta
import sys
from collections import OrderedDict
import configparser
from slips.core.database import __database__
import time
import ipaddress

def timing(f):
    """ Function to measure the time another function takes."""
    def wrap(*args):
        time1 = time.time()
        ret = f(*args)
        time2 = time.time()
        print('Function took {:.3f} ms'.format((time2-time1)*1000.0))
        return ret
    return wrap

# Profiler Process
class ProfilerProcess(multiprocessing.Process):
    """ A class to create the profiles for IPs and the rest of data """
    def __init__(self, inputqueue, outputqueue, config, width):
        self.name = 'Profiler'
        multiprocessing.Process.__init__(self)
        self.inputqueue = inputqueue
        self.outputqueue = outputqueue
        self.config = config
        self.width = width
        self.columns_defined = False
        self.timeformat = ''
        self.input_type = False
        # Read the configuration
        self.read_configuration()
        # Set the database output queue
        __database__.setOutputQueue(self.outputqueue)

    def print(self, text, verbose=1, debug=0):
        """ 
        Function to use to print text using the outputqueue of slips.
        Slips then decides how, when and where to print this text by taking all the prcocesses into account

        Input
         verbose: is the minimum verbosity level required for this text to be printed
         debug: is the minimum debugging level required for this text to be printed
         text: text to print. Can include format like 'Test {}'.format('here')
        
        If not specified, the minimum verbosity level required is 1, and the minimum debugging level is 0
        """

        vd_text = str(int(verbose) * 10 + int(debug))
        self.outputqueue.put(vd_text + '|' + self.name + '|[' + self.name + '] ' + str(text))

    def read_configuration(self):
        """ Read the configuration file for what we need """
        # Get the home net if we have one from the config
        try:
            self.home_net = ipaddress.ip_network(self.config.get('parameters', 'home_network'))
        except (configparser.NoOptionError, configparser.NoSectionError, NameError):
            # There is a conf, but there is no option, or no section or no configuration file specified
            self.home_net = False

        # Get the time window width, if it was not specified as a parameter 
        if not self.width:
            try:
                data = self.config.get('parameters', 'time_window_width')
                self.width = float(data)
            except ValueError:
                # Its not a float
                if 'only_one_tw' in data:
                    # Only one tw. Width is 10 9s, wich is ~11,500 days, ~311 years
                    self.width = 9999999999
            except configparser.NoOptionError:
                # By default we use 300 seconds, 5minutes
                self.width = 300.0
            except (configparser.NoOptionError, configparser.NoSectionError, NameError):
                # There is a conf, but there is no option, or no section or no configuration file specified
                self.width = 300.0
        # Limit any width to be > 0. By default we use 300 seconds, 5minutes
        elif self.width < 0:
            self.width = 300.0
        else:
            self.width = 300.0
        # Report the time window width
        if self.width == 9999999999:
            self.outputqueue.put("10|profiler|Time Windows Width used: Only 1 time windows. Dates in the names of files are 100 years in the past.".format(self.width))
        else:
            self.outputqueue.put("10|profiler|Time Windows Width used: {} seconds.".format(self.width))

        # Get the format of the time in the flows
        try:
            self.timeformat = config.get('timestamp', 'format')
        except (configparser.NoOptionError, configparser.NoSectionError, NameError):
            # There is a conf, but there is no option, or no section or no configuration file specified
            self.timeformat = '%Y/%m/%d %H:%M:%S.%f'

        ##
        # Get the direction of analysis
        try:
            self.analysis_direction = self.config.get('parameters', 'analysis_direction')
        except (configparser.NoOptionError, configparser.NoSectionError, NameError):
            # There is a conf, but there is no option, or no section or no configuration file specified
            # By default 
            self.analysis_direction = 'all'

    def define_type(self, line):
        """ 
        Try to define very fast the type of input 
        Heuristic detection: dict (zeek from pcap of int), json (suricata), or csv (argus), or TAB separated (conn.log only from zeek)?
        Bro actually gives us json, but it was already coverted into a dict 
        in inputProcess
        Outputs can be: zeek, suricata, argus, zeek-tabs
        """
        try:
            if type(line) == dict:
                self.input_type = 'zeek'
            else:
                try:
                    data = json.loads(line)
                    if data['event_type'] == 'flow':
                        self.input_type = 'suricata'
                except ValueError:
                    nr_commas = len(line.split(','))
                    nr_tabs = len(line.split('	'))
                    if nr_commas > nr_tabs:
                        # Commas is the separator
                        self.separator = ','
                        self.input_type = 'argus'
                    elif nr_tabs > nr_commas:
                        # Tabs is the separator
                        # Probably a conn.log file alone from zeek
                        self.separator = '	'
                        self.input_type = 'zeek-tabs'
        except Exception as inst:
            self.print('\tProblem in define_type()', 0, 1)
            self.print(str(type(inst)), 0, 1)
            self.print(str(inst), 0, 1)
            sys.exit(1)

    def define_columns(self, line):
        """ Define the columns for Argus and Zeek-tabs from the line received """
        # These are the indexes for later fast processing
        self.column_idx = {}
        self.column_idx['starttime'] = False
        self.column_idx['endtime'] = False
        self.column_idx['dur'] = False
        self.column_idx['proto'] = False
        self.column_idx['appproto'] = False
        self.column_idx['saddr'] = False
        self.column_idx['sport'] = False
        self.column_idx['dir'] = False
        self.column_idx['daddr'] = False
        self.column_idx['dport'] = False
        self.column_idx['state'] = False
        self.column_idx['pkts'] = False
        self.column_idx['spkts'] = False
        self.column_idx['dpkts'] = False
        self.column_idx['bytes'] = False
        self.column_idx['sbytes'] = False
        self.column_idx['dbytes'] = False  

        try:
            nline = line.strip().split(self.separator)
            for field in nline:
                if 'time' in field.lower():
                    self.column_idx['starttime'] = nline.index(field)
                elif 'dur' in field.lower():
                    self.column_idx['dur'] = nline.index(field)
                elif 'proto' in field.lower():
                    self.column_idx['proto'] = nline.index(field)
                elif 'srca' in field.lower():
                    self.column_idx['saddr'] = nline.index(field)
                elif 'sport' in field.lower():
                    self.column_idx['sport'] = nline.index(field)
                elif 'dir' in field.lower():
                    self.column_idx['dir'] = nline.index(field)
                elif 'dsta' in field.lower():
                    self.column_idx['daddr'] = nline.index(field)
                elif 'dport' in field.lower():
                    self.column_idx['dport'] = nline.index(field)
                elif 'state' in field.lower():
                    self.column_idx['state'] = nline.index(field)
                elif 'totpkts' in field.lower():
                    self.column_idx['pkts'] = nline.index(field)
                elif 'totbytes' in field.lower():
                    self.column_idx['bytes'] = nline.index(field)
            # Some of the fields were not found probably, 
            # so just delete them from the index if their value is False. 
            # If not we will believe that we have data on them
            # We need a temp dict because we can not change the size of dict while analyzing it
            temp_dict = {}
            for i in self.column_idx:
                if type(self.column_idx[i]) == bool and self.column_idx[i] == False:
                    continue
                temp_dict[i] = self.column_idx[i]
            self.column_idx = temp_dict
        except Exception as inst:
            self.print('\tProblem in define_columns()', 0, 1)
            self.print(str(type(inst)), 0, 1)
            self.print(str(inst), 0, 1)
            sys.exit(1)

    def process_zeek_input(self, line):
        """
        Process the line and extract columns for zeek
        Its a dictionary
        """
        if 'conn' in line['type']:
            # {'ts': 1538080852.403669, 'uid': 'Cewh6D2USNVtfcLxZe', 'id.orig_h': '192.168.2.12', 'id.orig_p': 56343, 'id.resp_h': '192.168.2.1', 'id.resp_p': 53, 'proto': 'udp', 'service': 'dns', 'duration': 0.008364, 'orig_bytes': 30, 'resp_bytes': 94, 'conn_state': 'SF', 'missed_bytes': 0, 'history': 'Dd', 'orig_pkts': 1, 'orig_ip_bytes': 58, 'resp_pkts': 1, 'resp_ip_bytes': 122, 'orig_l2_addr': 'b8:27:eb:6a:47:b8', 'resp_l2_addr': 'a6:d1:8c:1f:ce:64', 'type': './zeek_files/conn'}
            self.column_values = {}
            self.column_values['type'] = 'conn'
            self.column_values['starttime'] = datetime.fromtimestamp(line['ts'])
            try:
                self.column_values['dur'] = line['duration']
            except KeyError:
                self.column_values['dur'] = 0
            self.column_values['endtime'] = self.column_values['starttime'] + timedelta(self.column_values['dur'])
            self.column_values['proto'] = line['proto']
            try:
                self.column_values['appproto'] = line['service']
            except KeyError:
                # no service recognized
                self.column_values['appproto'] = ''
            self.column_values['saddr'] = line['id.orig_h']
            self.column_values['sport'] = line['id.orig_p']
            self.column_values['dir'] = '->'
            self.column_values['daddr'] = line['id.resp_h']
            self.column_values['dport'] = line['id.resp_p']
            self.column_values['state'] = line['conn_state']
            try:
                self.column_values['spkts'] = line['orig_pkts']
            except KeyError:
                self.column_values['spkts'] = 0
            try:
                self.column_values['dpkts'] = line['resp_pkts']
            except KeyError:
                self.column_values['dpkts'] = 0
            self.column_values['pkts'] = self.column_values['spkts'] + self.column_values['dpkts']
            try:
                self.column_values['sbytes'] = line['orig_bytes']
            except KeyError:
                self.column_values['sbytes'] = 0
            try:
                self.column_values['dbytes'] = line['resp_bytes']
            except KeyError:
                self.column_values['dbytes'] = 0
            self.column_values['bytes'] = self.column_values['sbytes'] + self.column_values['dbytes'] 
            self.column_values['uid'] = line['uid']
            try:
                self.column_values['state_hist'] = line['history']
            except KeyError:
                self.column_values['state_hist'] = self.column_values['state']
            self.column_values['smac'] = line['orig_l2_addr']
            self.column_values['dmac'] = line['resp_l2_addr']
        elif 'http' in line['type']:
            self.column_values = {}
            self.column_values['type'] = 'http'
        elif 'dns' in line['type']:
            #{"ts":1538080852.403669,"uid":"CtahLT38vq7vKJVBC3","id.orig_h":"192.168.2.12","id.orig_p":56343,"id.resp_h":"192.168.2.1","id.resp_p":53,"proto":"udp","trans_id":2,"rtt":0.008364,"query":"pool.ntp.org","qclass":1,"qclass_name":"C_INTERNET","qtype":1,"qtype_name":"A","rcode":0,"rcode_name":"NOERROR","AA":false,"TC":false,"RD":true,"RA":true,"Z":0,"answers":["185.117.82.70","212.237.100.250","213.251.52.107","183.177.72.201"],"TTLs":[42.0,42.0,42.0,42.0],"rejected":false}
            self.column_values = {}
            self.column_values['type'] = 'dns'
        elif 'ssh' in line['type']:
            self.column_values = {}
            self.column_values['type'] = 'ssh'
        elif 'ssl' in line['type']:
            self.column_values = {}
            self.column_values['type'] = 'ssl'
        elif 'irc' in line['type']:
            self.column_values = {}
            self.column_values['type'] = 'irc'
        elif 'long' in line['type']:
            self.column_values = {}
            self.column_values['type'] = 'long'

    def process_argus_input(self, line):
        """
        Process the line and extract columns for argus
        """
        self.column_values = {}
        self.column_values['starttime'] = False
        self.column_values['endtime'] = False
        self.column_values['dur'] = False
        self.column_values['proto'] = False
        self.column_values['appproto'] = False
        self.column_values['saddr'] = False
        self.column_values['sport'] = False
        self.column_values['dir'] = False
        self.column_values['daddr'] = False
        self.column_values['dport'] = False
        self.column_values['state'] = False
        self.column_values['pkts'] = False
        self.column_values['spkts'] = False
        self.column_values['dpkts'] = False
        self.column_values['bytes'] = False
        self.column_values['sbytes'] = False
        self.column_values['dbytes'] = False
        self.column_values['type'] = 'argus'

        # Read the lines fast
        nline = line.strip().split(self.separator)
        try:
            self.column_values['starttime'] = datetime.strptime(nline[self.column_idx['starttime']], self.timeformat)
        except KeyError:
            pass
        try:
            self.column_values['endtime'] = nline[self.column_idx['endtime']]
        except KeyError:
            pass
        try:
            self.column_values['dur'] = nline[self.column_idx['dur']]
        except KeyError:
            pass
        try:
            self.column_values['proto'] = nline[self.column_idx['proto']]
        except KeyError:
            pass
        try:
            self.column_values['appproto'] = nline[self.column_idx['appproto']]
        except KeyError:
            pass
        try:
            self.column_values['saddr'] = nline[self.column_idx['saddr']]
        except KeyError:
            pass
        try:
            self.column_values['sport'] = nline[self.column_idx['sport']]
        except KeyError:
            pass
        try:
            self.column_values['dir'] = nline[self.column_idx['dir']]
        except KeyError:
            pass
        try:
            self.column_values['daddr'] = nline[self.column_idx['daddr']]
        except KeyError:
            pass
        try:
            self.column_values['dport'] = nline[self.column_idx['dport']]
        except KeyError:
            pass
        try:
            self.column_values['state'] = nline[self.column_idx['state']]
        except KeyError:
            pass
        try:
            self.column_values['pkts'] = nline[self.column_idx['pkts']]
        except KeyError:
            pass
        try:
            self.column_values['spkts'] = nline[self.column_idx['spkts']]
        except KeyError:
            pass
        try:
            self.column_values['dpkts'] = nline[self.column_idx['dpkts']]
        except KeyError:
            pass
        try:
            self.column_values['bytes'] = nline[self.column_idx['bytes']]
        except KeyError:
            pass
        try:
            self.column_values['sbytes'] = nline[self.column_idx['sbytes']]
        except KeyError:
            pass
        try:
            self.column_values['dbytes'] = nline[self.column_idx['dbytes']]
        except KeyError:
            pass

    def add_flow_to_profile(self):
        """ 
        This is the main function that takes the columns of a flow and does all the magic to convert it into a working data in our system. 
        It includes checking if the profile exists and how to put the flow correctly.
        It interprets each colum 
        """
        try:
            if not 'conn' in self.column_values['type'] and not 'argus' in self.column_values['type']:
                return True
            #########
            # 1st. Get the data from the interpreted columns
            separator = __database__.getFieldSeparator()
            starttime = time.mktime(self.column_values['starttime'].timetuple())
            dur = self.column_values['dur']
            saddr = self.column_values['saddr']
            profileid = 'profile' + separator + str(saddr)
            sport = self.column_values['sport']
            daddr = self.column_values['daddr']
            dport = self.column_values['dport']
            sport = self.column_values['sport']
            proto = self.column_values['proto']
            state = self.column_values['state']
            pkts = self.column_values['pkts']
            allbytes = self.column_values['bytes']
            spkts = self.column_values['spkts']
            sbytes = self.column_values['sbytes']
            endtime = self.column_values['endtime']
            appproto = self.column_values['appproto']
            direction = self.column_values['dir']
            dpkts = self.column_values['dpkts']
            dbytes = self.column_values['dbytes']

            # Create the objects of IPs
            try:
                saddr_as_obj = ipaddress.IPv4Address(saddr) 
                daddr_as_obj = ipaddress.IPv4Address(daddr) 
                # Is ipv4
            except ipaddress.AddressValueError:
                # Is it ipv6?
                try:
                    saddr_as_obj = ipaddress.IPv6Address(saddr) 
                    daddr_as_obj = ipaddress.IPv6Address(daddr) 
                except ipaddress.AddressValueError:
                    # Its a mac
                    return False

     
            ##############
            # For Adding the profile only now

            # 2nd. Check home network
            # Check if the ip received (src_ip) is part of our home network. We only crate profiles for our home network
            if self.home_net and saddr_as_obj in self.home_net:
                # Its in our Home network

                # The steps for adding a flow in a profile should be
                # 1. Add the profile to the DB. If it already exists, nothing happens. So now profileid is the id of the profile to work with. 
                # The width is unique for all the timewindow in this profile. 
                # Also we only need to pass the width for registration in the DB. Nothing operational
                __database__.addProfile(profileid, starttime, self.width)

                # 3. For this profile, find the id in the databse of the tw where the flow belongs.
                twid = self.get_timewindow(starttime, profileid)

            elif self.home_net and saddr_as_obj not in self.home_net:
                # The src ip is not in our home net

                # Check that the dst IP is in our home net. Like the flow is 'going' to it.
                if daddr_as_obj in self.home_net:
                    self.outputqueue.put("07|profiler|[Profiler] Flow with dstip in homenet: srcip {}, dstip {}".format(saddr_as_obj, daddr_as_obj))
                    # The dst ip is in the home net. So register this as going to it
                    # 1. Get the profile of the dst ip.
                    rev_profileid = __database__.getProfileIdFromIP(daddr_as_obj)
                    if not rev_profileid:
                        # We do not have yet the profile of the dst ip that is in our home net
                        self.outputqueue.put("07|profiler|[Profiler] The dstip profile was not here... create")
                        # Create a reverse profileid for managing the data going to the dstip. 
                        # With the rev_profileid we can now work with data in relation to the dst ip
                        rev_profileid = 'profile' + separator + str(daddr_as_obj)
                        __database__.addProfile(rev_profileid, starttime, self.width)
                        # Try again
                        rev_profileid = __database__.getProfileIdFromIP(daddr_as_obj)
                        # For the profile to the dstip, find the id in the database of the tw where the flow belongs.
                        rev_twid = self.get_timewindow(starttime, rev_profileid)
                        if not rev_profileid:
                            # Too many errors. We should not be here
                            return False
                    self.outputqueue.put("07|profiler|[Profile] Profile for dstip {} : {}".format(daddr_as_obj, profileid))
                    # 2. For this profile, find the id in the databse of the tw where the flow belongs.
                    rev_twid = self.get_timewindow(starttime, profileid)
                elif daddr_as_obj not in self.home_net:
                    # The dst ip is also not part of our home net. So ignore completely
                    return False
            elif not self.home_net:
                # We don't have a home net, so create profiles for everyone

                # Add the profile for the srcip to the DB. If it already exists, nothing happens. So now profileid is the id of the profile to work with. 
                __database__.addProfile(profileid, starttime, self.width)
                # Add the profile for the dstip to the DB. If it already exists, nothing happens. So now rev_profileid is the id of the profile to work with. 
                rev_profileid = 'profile' + separator + str(daddr_as_obj)
                __database__.addProfile(rev_profileid, starttime, self.width)

                # For the profile from the srcip , find the id in the database of the tw where the flow belongs.
                twid = self.get_timewindow(starttime, profileid)
                # For the profile to the dstip, find the id in the database of the tw where the flow belongs.
                rev_twid = self.get_timewindow(starttime, rev_profileid)


            ##############
            # 4th Define help functions for storing data
            def store_features_going_out(profile, tw):
                """
                This is an internal function in the add_flow_to_profile function for adding the features going out of the profile
                """
                # Tuple
                tupleid = str(daddr_as_obj) + ':' + str(dport) + ':' + proto

                # Compute the symbol for this flow, for this TW, for this profile
                # FIX
                symbol = ('a', '2019-01-26--13:31:09', 1)

                # Add the out tuple
                __database__.add_out_tuple(profile, tw, tupleid, symbol)
                # Add the dstip
                __database__.add_out_dstips(profile, tw, daddr_as_obj, state, pkts, proto, dport)
                # Add the dstport
                __database__.add_out_dstport(profile, tw, dport, allbytes, sbytes, pkts, spkts, state, proto, daddr_as_obj)
                # Add the srcport
                __database__.add_out_srcport(profile, tw, sport)
                # Add the flow with all the fields interpreted
                __database__.add_flow(profileid=profile, twid=tw, stime=starttime, dur=dur, saddr=str(saddr_as_obj), sport=sport, daddr=str(daddr_as_obj), dport=dport, proto=proto, state=state, pkts=pkts, allbytes=allbytes, spkts=spkts, sbytes=sbytes, appproto=appproto)

            def store_features_going_in(profile, tw):
                """
                This is an internal function in the add_flow_to_profile function for adding the features going in of the profile
                """
                # Add the srcip
                __database__.add_in_srcips(profile, tw, saddr_as_obj)
                # Add the dstport
                __database__.add_in_dstport(profile, tw, dport)
                # Add the srcport
                __database__.add_in_srcport(profile, tw, sport)
                # Add the flow with all the fields interpreted
                __database__.add_flow(profileid=profile, twid=tw, stime=starttime, dur=dur, saddr=str(saddr_as_obj), sport=sport, daddr=str(daddr_as_obj), dport=dport, proto=proto, state=state, pkts=pkts, allbytes=allbytes, spkts=spkts, sbytes=sbytes, appproto=appproto)


            ##########################################
            # 5th. Store the data according to the paremeters
            # Now that we have the profileid and twid, add the data from the flow in this tw for this profile
            self.outputqueue.put("07|profiler|[Profiler] Storing data in the profile: {}".format(profileid))

            # In which analysis mode are we?
            # Mode 'out'
            if self.analysis_direction == 'out':
                # Only take care of the stuff going out. Here we don't keep track of the stuff going in
                # If we have a home net and the flow comes from it, or if we don't have a home net and we are in out out.
                if (self.home_net and saddr_as_obj in self.home_net) or not self.home_net:
                    store_features_going_out(profileid, twid)

            # Mode 'all'
            elif self.analysis_direction == 'all':
                # Take care of both the stuff going out and in. In case the profile is for the srcip and for the dstip 
                if not self.home_net:
                    # If we don't have a home net, just try to store everything coming OUT and IN to the IP
                    # Out features
                    store_features_going_out(profileid, twid)
                    # IN features
                    store_features_going_in(rev_profileid, rev_twid)

                # If we have a home net and the flow comes from it. Only the features going out of the IP
                elif self.home_net and saddr_as_obj in self.home_net:
                    store_features_going_out(profileid, twid)
                # If we have a home net and the flow comes to it. Only the features going in of the IP
                elif self.home_net and daddr_as_obj in self.home_net:
                    # The dstip was in the homenet. Add the src info to the dst profile
                    store_features_going_in(rev_profileid, rev_twid)

        except Exception as inst:
            # For some reason we can not use the output queue here.. check
            self.outputqueue.put("01|profiler|[Profile] Error in add_flow_to_profile profilerProcess.")
            self.outputqueue.put("01|profiler|[Profile] {}".format((type(inst))))
            self.outputqueue.put("01|profiler|[Profile] {}".format(inst))

    def compute_symbol(self, profileid, twid, tupleid, current_time, current_duration, current_size):
        """ 
        This function computes the new symbol for the tuple according to the original stratosphere ips model of letters
        Here we do not apply any detection model, we just create the letters as one more feature
        """
        try:
            current_duration = float(current_duration)
            current_size = int(current_size)
            self.outputqueue.put("01|profiler|[Profile] Starting compute symbol. Tupleid {}, time:{} ({}), dur:{}, size:{}".format(tupleid, current_time, type(current_time), current_duration, current_size))
            # Variables for computing the symbol of each tuple
            T2 = False
            TD = False
            # Thresholds learng from Stratosphere ips first version
            # Timeout time, after 1hs
            tto = timedelta(seconds=3600)
            tt1 = float(1.05)
            tt2 = float(1.3)
            tt3 = float(5)
            td1 = float(0.1)
            td2 = float(10)
            ts1 = float(250)
            ts2 = float(1100)
            letter = ''
            symbol = ''
            timechar = ''

            # Get T1 (the time diff between the past flow and the past-past flow) from this tuple. T1 is a float in the db. Also get the time of the last flow in this tuple. In the DB prev time is a str
            self.outputqueue.put("01|profiler|[Profile] AAA ")
            (T1, previous_time) = __database__.getT2ForProfileTW(profileid, twid, tupleid)
            ## BE SURE THAT HERE WE RECEIVE THE PROPER DATA
            #T1 = timedelta(seconds=10)
            #previous_time = datetime.now() - timedelta(seconds=3600)
            self.outputqueue.put("01|profiler|[Profile] T1:{}, previous_time:{}".format(T1, previous_time))


            def compute_periodicity():
                """ Function to compute the periodicity """
                # If either T1 or T2 are False
                #if (isinstance(T1, bool) and T1 == False) or (isinstance(T2, bool) and T2 == False):
                if T1 == False or T2 == False:
                    periodicity = -1
                elif T2 >= tto:
                    t2_in_hours = T2.total_seconds() / tto.total_seconds()
                    # Should be int always
                    for i in range(int(t2_in_hours)):
                        # Add the 0000 to the symbol object
                        symbol += '0'
                # Why to recompute the 0000 with T1 again??? this should have been done when processing the previous flow
                #elif T1 >= tto:
                    #t1_in_hours = T1.total_seconds() / tto.total_seconds()
                    ## Should be int always
                    #for i in range(int(t1_in_hours)):
                        #state += '0'
                if not isinstance(T1, bool) and not isinstance(T2, bool):
                    try:
                        if T2 >= T1:
                            TD = timedelta(seconds=(T2.total_seconds() / T1.total_seconds())).total_seconds()
                        else:
                            TD = timedelta(seconds=(T1.total_seconds() / T2.total_seconds())).total_seconds()
                    except ZeroDivisionError:
                        TD = 1
                    # Decide the periodic based on TD and the thresholds
                    if TD <= tt1:
                        # Strongly periodicity
                        return 1
                    elif TD < tt2:
                        # Weakly periodicity
                        return 2
                    elif TD < tt3:
                        # Weakly not periodicity
                        return 3
                    else:
                        return 4

            def compute_duration():
                """ Function to compute letter of the duration """
                if current_duration <= td1:
                    return 1
                elif current_duration > td1 and current_duration <= td2:
                    return 2
                elif current_duration > td2:
                    return 3

            def compute_size():
                """ Function to compute letter of the size """
                if current_size <= ts1:
                    return 1
                elif current_size > ts1 and current_size <= ts2:
                    return 2
                elif current_size > ts2:
                    return 3

            def compute_letter():
                """ Function to compute letter """
                if periodicity == -1:
                    if size == 1:
                        if duration == 1:
                            return '1'
                        elif duration == 2:
                            return '2'
                        elif duration == 3:
                            return '3'
                    elif size == 2:
                        if duration == 1:
                            return '4'
                        elif duration == 2:
                            return '5'
                        elif duration == 3:
                            return '6'
                    elif size == 3:
                        if duration == 1:
                            return '7'
                        elif duration == 2:
                            return '8'
                        elif duration == 3:
                            return '9'
                elif periodicity == 1:
                    if size == 1:
                        if duration == 1:
                            return 'a'
                        elif duration == 2:
                            return 'b'
                        elif duration == 3:
                            return 'c'
                    elif size == 2:
                        if duration == 1:
                            return 'd'
                        elif duration == 2:
                            return 'e'
                        elif duration == 3:
                            return 'f'
                    elif size == 3:
                        if duration == 1:
                            return 'g'
                        elif duration == 2:
                            return 'h'
                        elif duration == 3:
                            return 'i'
                elif periodicity == 2:
                    if size == 1:
                        if duration == 1:
                            return 'A'
                        elif duration == 2:
                            return 'B'
                        elif duration == 3:
                            return 'C'
                    elif size == 2:
                        if duration == 1:
                            return 'D'
                        elif duration == 2:
                            return 'E'
                        elif duration == 3:
                            return 'F'
                    elif size == 3:
                        if duration == 1:
                            return 'G'
                        elif duration == 2:
                            return 'H'
                        elif duration == 3:
                            return 'I'
                elif periodicity == 3:
                    if size == 1:
                        if duration == 1:
                            return 'r'
                        elif duration == 2:
                            return 's'
                        elif duration == 3:
                            return 't'
                    elif size == 2:
                        if duration == 1:
                            return 'u'
                        elif duration == 2:
                            return 'v'
                        elif duration == 3:
                            return 'w'
                    elif size == 3:
                        if duration == 1:
                            return 'x'
                        elif duration == 2:
                            return 'y'
                        elif duration == 3:
                            return 'z'
                elif periodicity == 4:
                    if size == 1:
                        if duration == 1:
                            return 'R'
                        elif duration == 2:
                            return 'S'
                        elif duration == 3:
                            return 'T'
                    elif size == 2:
                        if duration == 1:
                            return 'U'
                        elif duration == 2:
                            return 'V'
                        elif duration == 3:
                            return 'W'
                    elif size == 3:
                        if duration == 1:
                            return 'X'
                        elif duration == 2:
                            return 'Y'
                        elif duration == 3:
                            return 'Z'

            def compute_timechar():
                """ Function to compute the timechar """
                if not isinstance(T2, bool):
                    if T2 <= timedelta(seconds=5):
                        return  '.'
                    elif T2 <= timedelta(seconds=60):
                        return ','
                    elif T2 <= timedelta(seconds=300):
                        return '+'
                    elif T2 <= timedelta(seconds=3600):
                        return '*'

            # Here begins the function's code
            try:
                # Update value of T2
                T2 = current_time - previous_time
                # Are flows sorted?
                if T2.total_seconds() < 0:
                    # Flows are not sorted!
                    # What is going on here when the flows are not ordered?? Are we losing flows?
                    # Put a warning
                    pass
            except TypeError:
                T2 = False
            self.outputqueue.put("01|profiler|[Profile] T2:{}".format(T2))

            # Compute the rest
            periodicity = compute_periodicity()
            self.outputqueue.put("01|profiler|[Profile] Periodicity:{}".format(periodicity))
            duration = compute_duration()
            self.outputqueue.put("01|profiler|[Profile] Duration:{}".format(duration))
            size = compute_size()
            self.outputqueue.put("01|profiler|[Profile] Size:{}".format(size))
            letter = compute_letter()
            self.outputqueue.put("01|profiler|[Profile] Letter:{}".format(letter))
            timechar = compute_timechar()
            self.outputqueue.put("01|profiler|[Profile] TimeChar:{}".format(timechar))

            symbol = letter + timechar
            T1 = T1.total_seconds()
            current_time = current_time.strftime(self.timeformat)
            self.outputqueue.put("01|profiler|[Profile] To Store. symbol: {}, current_time: {}, T1: {} ({})".format(symbol, current_time, T1, type(T1)))
            # Return the symbol, the current time of the flow and the T1 value
            return (symbol, str(current_time), T1)
            # End of the compute_symbol function
        except Exception as inst:
            # For some reason we can not use the output queue here.. check
            self.outputqueue.put("01|profiler|[Profile] Error in compute_symbol in profilerProcess.")
            self.outputqueue.put("01|profiler|[Profile] {}".format(type(inst)))
            self.outputqueue.put("01|profiler|[Profile] {}".format(inst))


    def get_timewindow(self, flowtime, profileid):
        """" 
        This function should get the id of the TW in the database where the flow belong.
        If the TW is not there, we create as many tw as necessary in the future or past until we get the correct TW for this flow.
        - We use this function to avoid retrieving all the data from the DB for the complete profile. We use a separate table for the TW per profile.
        -- Returns the time window id
        THIS IS NOT WORKING:
        - The empty profiles in the middle are not being created!!!
        - The Dtp ips are stored in the first time win
        """
        try:
            # First check of we are not in the last TW. Since this will be the majority of cases
            try:
                [(lasttwid, lasttw_start_time)] = __database__.getLastTWforProfile(profileid)
                lasttw_start_time = float(lasttw_start_time)
                lasttw_end_time = lasttw_start_time + self.width
                flowtime = float(flowtime)
                self.outputqueue.put("04|profiler|[Profiler] The last TW id was {}. Start:{}. End: {}".format(lasttwid, lasttw_start_time, lasttw_end_time))
                # There was a last TW, so check if the current flow belongs here.
                if lasttw_end_time > flowtime and lasttw_start_time <= flowtime:
                    self.outputqueue.put("04|profiler|[Profiler] The flow ({}) is on the last time window ({})".format(flowtime, lasttw_end_time))
                    twid = lasttwid
                elif lasttw_end_time <= flowtime:
                    # The flow was not in the last TW, its NEWER than it
                    self.outputqueue.put("04|profiler|[Profiler] The flow ({}) is NOT on the last time window ({}). Its newer".format(flowtime, lasttw_end_time))
                    amount_of_new_tw = int((flowtime - lasttw_end_time) / self.width)
                    self.outputqueue.put("04|profiler|[Profiler] We have to create {} empty TWs in the midle.".format(amount_of_new_tw))
                    temp_end = lasttw_end_time
                    for id in range(0, amount_of_new_tw + 1):
                        new_start = temp_end 
                        twid = __database__.addNewTW(profileid, new_start)
                        self.outputqueue.put("04|profiler|[Profiler] Creating the TW id {}. Start: {}.".format(twid, new_start))
                        temp_end = new_start + self.width
                    # Now get the id of the last TW so we can return it
                elif lasttw_start_time > flowtime:
                    # The flow was not in the last TW, its OLDER that it
                    self.outputqueue.put("04|profiler|[Profiler] The flow ({}) is NOT on the last time window ({}). Its older".format(flowtime, lasttw_end_time))
                    # Find out if we already have this TW in the past
                    data = __database__.getTWforScore(profileid, flowtime)
                    if data:
                        # We found a TW where this flow belongs to
                        (twid, tw_start_time) = data
                        return twid
                    else:
                        # There was no TW that included the time of this flow, so create them in the past
                        # How many new TW we need in the past?
                        amount_of_new_tw = int((lasttw_end_time - flowtime) / self.width)
                        amount_of_current_tw = __database__.getamountTWsfromProfile(profileid)
                        diff = amount_of_new_tw - amount_of_current_tw
                        self.outputqueue.put("05|profiler|[Profiler] We need to create {} TW before the first".format(diff))
                        # Get the first TW
                        [(firsttwid, firsttw_start_time)] = __database__.getFirstTWforProfile(profileid)
                        firsttw_start_time = float(firsttw_start_time)
                        # The start of the new older TW should be the first - the width
                        temp_start = firsttw_start_time - self.width
                        for id in range(0, diff + 1):
                            new_start = temp_start
                            # The method to add an older TW is the same as to add a new one, just the starttime changes
                            twid = __database__.addNewOlderTW(profileid, new_start)
                            self.outputqueue.put("02|profiler|[Profiler] Creating the new older TW id {}. Start: {}.".format(twid, new_start))
                            temp_start = new_start - self.width
            except ValueError:
                # There is no last tw. So create the first TW
                # If the option for only-one-tw was selected, we should create the TW at least 100 years before the flowtime, to cover for
                # 'flows in the past'. Which means we should cover for any flow that is coming later with time before the first flow
                if self.width == 9999999999:
                    # Seconds in 1 year = 31536000
                    startoftw = float(flowtime - (31536000 * 100))
                else:
                    startoftw = float(flowtime)
                # Add this TW, of this profile, to the DB
                twid = __database__.addNewTW(profileid, startoftw)
                #self.outputqueue.put("01|profiler|First TW ({}) created for profile {}.".format(twid, profileid))
            return twid
        except Exception as e:
            self.outputqueue.put("01|profiler|[Profile] Error in get_timewindow().")
            self.outputqueue.put("01|profiler|[Profile] {}".format(e))

    def run(self):
        # Main loop function
        try:
            rec_lines = 0
            while True:
                line = self.inputqueue.get()
                if 'stop' == line:
                    self.outputqueue.put("01|profiler|[Profile] Stopping Profiler Process. Received {} lines ({})".format(rec_lines, datetime.now().strftime('%Y-%m-%d--%H:%M:%S')))
                    return True
                else:
                    # Received new input data
                    # Extract the columns smartly
                    self.outputqueue.put("03|profiler|[Profile] < Received Line: {}".format(line))
                    rec_lines += 1
                    if not self.input_type:
                        # Find the type of input received
                        # This line will be discarded because 
                        self.define_type(line)
                        # We should do this before checking the type of input so we don't lose the first line of input

                    # What type of input do we have?
                    if self.input_type == 'zeek':
                        #self.print('Zeek line')
                        self.process_zeek_input(line)
                        # Add the flow to the profile
                        self.add_flow_to_profile()

                    elif self.input_type == 'argus':
                        #self.print('Argus line')
                        # Argus puts the definition of the columns on the first line only
                        # So read the first line and define the columns

                        # Are the columns defined?
                        try:
                            temp = self.column_idx['starttime']
                            # Yes
                            # Quickly process all lines
                            self.process_argus_input(line)
                            # Add the flow to the profile
                            self.add_flow_to_profile()
                        except AttributeError:
                            # No. Define columns. Do not add this line to profile, its only headers
                            self.define_columns(line)

                    elif self.input_type == 'suricata':
                        #self.print('Suricata line')
                        self.process_suricata_input(line)
                        # Add the flow to the profile
                        self.add_flow_to_profile()

                    elif self.input_type == 'zeek-tabs':
                        #self.print('Zeek-tabs line')
                        self.process_zeek_tabs_input(line)
                        # Add the flow to the profile
                        self.add_flow_to_profile()
        except KeyboardInterrupt:
            self.outputqueue.put("01|profiler|[Profile] Received {} lines.".format(rec_lines))
            return True
        except Exception as inst:
            self.outputqueue.put("01|profiler|[Profile] Error. Stopped Profiler Process. Received {} lines".format(rec_lines))
            self.outputqueue.put("01|profiler|\tProblem with Profiler Process.")
            self.outputqueue.put("01|profiler|"+str(type(inst)))
            self.outputqueue.put("01|profiler|"+str(inst.args))
            self.outputqueue.put("01|profiler|"+str(inst))
            return True
