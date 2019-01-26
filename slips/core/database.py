import redis
# delete these
import time
from datetime import datetime
from datetime import timedelta
import json


# Struture of the DB
# Set 'profile'
#  Holds a set of all profile ids
# For each profile, there is a set for the timewindows. The name of the sets is the names of profiles
# The data for a profile in general is hold in a hash
# The data for each timewindow in a profile is hold in a hash
 # profile|10.0.0.1|timewindow1
 # In this hash there are strings:
  # dstips_in -> '{'1.1.1.1':10, '2.2.2.2':20}'
  # srcips_in -> '{'3.3.3.3':30, '4.4.4.4':40}'
  # dstports_in -> '{'22':30, '21':40}'
  # dstports_in -> '{'22':30, '21':40}'
  # dstips_out -> '{'1.1.1.1':10, '2.2.2.2':20}'
  # srcips_out -> '{'3.3.3.3':30, '4.4.4.4':40}'
  # dstports_out -> '{'22':30, '21':40}'
  # dstports_out -> '{'22':30, '21':40}'


def timing(f):
    """ Function to measure the time another function takes."""
    def wrap(*args):
        time1 = time.time()
        ret = f(*args)
        time2 = time.time()
        self.outputqueue.put('01|database|Function took {:.3f} ms'.format((time2-time1)*1000.0))
        return ret
    return wrap

class Database(object):
    """ Database object management """

    def __init__(self):
        # Get the connection to redis database
        self.r = redis.StrictRedis(host='localhost', port=6379, db=0, charset="utf-8", decode_responses=True) #password='password')
        # For now, do not remember between runs of slips. Just delete the database when we start with flushdb
        self.r.flushdb()
        self.separator = '_'

    def setOutputQueue(self, outputqueue):
        """ Set the output queue"""
        self.outputqueue = outputqueue

    def addProfile(self, profileid, starttime, duration):
        """ 
        Add a new profile to the DB. Both the list of profiles and the hasmap of profile data
        Profiles are stored in two structures. A list of profiles (index) and individual hashmaps for each profile (like a table)
        Duration is only needed for registration purposes in the profile. Nothing operational
        """
        try:
            if not self.r.sismember('profiles', str(profileid)):
                # Add the profile to the index. The index is called 'profiles'
                self.r.sadd('profiles', str(profileid))
                # Create the hashmap with the profileid. The hasmap of each profile is named with the profileid
                self.r.hset(profileid, 'Starttime', starttime)
                # For now duration of the TW is fixed
                self.r.hset(profileid, 'duration', duration)
                # The name of the list with the dstips
                #self.r.hset(profileid, 'DstIps', 'DstIps')
        except redis.exceptions.ResponseError as inst:
            self.outputqueue.put('00|database|Error in addProfile in database.py')
            self.outputqueue.put('00|database|{}'.format(type(inst)))
            self.outputqueue.put('00|database|{}'.format(inst))

    def getProfileIdFromIP(self, daddr_as_obj):
        """ Receive an IP and we want the profileid"""
        try:
            temp_id = 'profile' + self.separator + str(daddr_as_obj)
            data = self.r.sismember('profiles', temp_id)
            if data:
                return temp_id
            return False
        except redis.exceptions.ResponseError as inst:
            self.outputqueue.put('00|database|error in addprofileidfromip in database.py')
            self.outputqueue.put('00|database|{}'.format(type(inst)))
            self.outputqueue.put('00|database|{}'.format(inst))

    def getProfiles(self):
        """ Get a list of all the profiles """
        profiles = self.r.smembers('profiles')
        if profiles != set():
            return profiles
        else:
            return {}

    def getProfileData(self, profileid):
        """ Get all the data for this particular profile.
        Returns:
        A json formated representation of the hashmap with all the data of the profile
            
        """
        profile = self.r.hgetall(profileid)
        if profile != set():
            return profile
        else:
            return False

    def getTWsfromProfile(self, profileid):
        """
        Receives a profile id and returns the list of all the TW in that profile

        """
        return self.r.zrange('tws' + profileid, 0, -1, withscores=True)

    def getamountTWsfromProfile(self, profileid):
        """
        Receives a profile id and returns the list of all the TW in that profile

        """
        return len(self.r.zrange('tws' + profileid, 0, -1, withscores=True))

    def getSrcIPsfromProfileTW(self, profileid, twid):
        """
        Get the src ip for a specific TW for a specific profileid
        """
        data = self.r.hget(profileid + self.separator + twid, 'SrcIPs')
        return data

    def getDstIPsfromProfileTW(self, profileid, twid):
        """
        Get the dst ip for a specific TW for a specific profileid
        """
        data = self.r.hget(profileid + self.separator + twid, 'DstIPs')
        return data

    def getT2ForProfileTW(self, profileid, twid, tupleid):
        """
        Get T1 and the previous_time for this previous_time, twid and tupleid
        """
        try:
            self.outputqueue.put('01|database|[DB] BB: {}, {}, {}'.format(profileid, twid, tupleid))
            hash_id = profileid + self.separator + twid
            data = self.r.hget(hash_id, 'OutTuples')
            if not data:
                return (False, False)
            self.outputqueue.put('01|database|[DB] Data in the tuple: {}'.format(data[tupleid]))
            ( _ , previous_time, T1) = data[tupleid]
            return (previous_time, T1)
        except Exception as e:
            self.outputqueue.put('01|database|[DB] Error in getT2ForProfileTW in database.py')
            self.outputqueue.put('01|database|[DB] {}'.format(type(e)))
            self.outputqueue.put('01|database|[DB] {}'.format(e))

    def hasProfile(self, profileid):
        """ Check if we have the given profile """
        return self.r.sismember('profiles', profileid)

    def getProfilesLen(self):
        """ Return the amount of profiles. Redis should be faster than python to do this count """
        return self.r.scard('profiles') 
   
    def getLastTWforProfile(self, profileid):
        """ Return the last TW id and the time for the given profile id """
        data = self.r.zrange('tws' + profileid, -1, -1, withscores=True)
        return data

    def getFirstTWforProfile(self, profileid):
        """ Return the first TW id and the time for the given profile id """
        data = self.r.zrange('tws' + profileid, 0, 0, withscores=True)
        return data

    def getTWforScore(self, profileid, time):
        """ Return the TW id and the time for the TW that includes the given time.
        The score in the DB is the start of the timewindow, so we should search a TW that includes 
        the given time by making sure the start of the TW is < time, and the end of the TW is > time.
        """
        # [-1] so we bring the last TW that matched this time.
        data = self.r.zrangebyscore('tws' + profileid, 0, float(time), withscores=True, start=0, num=-1)[-1]
        return data

    def addNewOlderTW(self, profileid, startoftw):
        try:
            """ 
            Creates or adds a new timewindow that is OLDER than the first we have
            Return the id of the timewindow just created
            """
            # Get the first twid and obtain the new tw id
            try:
                (firstid, firstid_time) = self.getFirstTWforProfile(profileid)[0]
                # We have a first id
                # Decrement it!!
                twid = 'timewindow' + str(int(firstid.split('timewindow')[1]) - 1)
            except IndexError:
                # Very weird error, since the first TW MUST exist. What are we doing here?
                pass
            # Add the new TW to the index of TW
            data = {}
            data[str(twid)] = float(startoftw)
            self.r.zadd('tws' + profileid, data)
            self.outputqueue.put('04|database|[DB]: Created and added to DB the new older TW with id {}. Time: {} '.format(twid, startoftw))
            # The creation of a TW now does not imply that it was modified. You need to put data to mark is at modified
            return twid
        except redis.exceptions.ResponseError as e:
            self.outputqueue.put('00|database|error in addNewOlderTW in database.py')
            self.outputqueue.put('00|database|{}'.format(type(inst)))
            self.outputqueue.put('00|database|{}'.format(inst))

    def addNewTW(self, profileid, startoftw):
        try:
            """ 
            Creates or adds a new timewindow to the list of tw for the given profile
            Add the twid to the ordered set of a given profile 
            Return the id of the timewindow just created
            """
            # Get the last twid and obtain the new tw id
            try:
                (lastid, lastid_time) = self.getLastTWforProfile(profileid)[0]
                # We have a last id
                # Increment it
                twid = 'timewindow' + str(int(lastid.split('timewindow')[1]) + 1)
            except IndexError:
                # There is no first TW, create it
                twid = 'timewindow1'
            # Add the new TW to the index of TW
            data = {}
            data[str(twid)] = float(startoftw)
            self.r.zadd('tws' + profileid, data)
            self.outputqueue.put('04|database|[DB]: Created and added to DB the TW with id {}. Time: {} '.format(twid, startoftw))
            # The creation of a TW now does not imply that it was modified. You need to put data to mark is at modified
            return twid
        except redis.exceptions.ResponseError as e:
            self.outputqueue.put('01|database|Error in addNewTW')
            self.outputqueue.put('01|database|{}'.format(e))

    def getTimeTW(self, profileid, twid):
        """ Return the time when this TW in this profile was created """
        # Get all the TW for this profile
        # We need to encode it to 'search' because the data in the sorted set is encoded
        data = self.r.zscore('tws' + profileid, twid.encode('utf-8'))
        return data

    def getAmountTW(self, profileid):
        """ Return the amount of tw for this profile id """
        return self.r.zcard('tws'+profileid)

    def getModifiedTW(self):
        """ Return all the list of modified tw """
        return self.r.smembers('ModifiedTW')

    def wasProfileTWModified(self, profileid, twid):
        """ Retrieve from the db if this TW of this profile was modified """
        data = self.r.sismember('ModifiedTW', profileid + self.separator + twid)
        if not data:
            # If for some reason we don't have the modified bit set, then it was not modified.
            data = 0
        return bool(data)

    def markProfileTWAsNotModified(self, profileid, twid):
        """ Mark a TW in a profile as not modified """
        self.r.srem('ModifiedTW', profileid + self.separator + twid)

    def markProfileTWAsModified(self, profileid, twid):
        """ 
        Mark a TW in a profile as not modified 
        As a side effect, it can create it if its not there
        """
        self.r.sadd('ModifiedTW', profileid + self.separator + twid)

    def add_out_dstips(self, profileid, twid, daddr_as_obj):
        """
        Function if the flow is going out from the profile IP
        Add the dstip to this tw in this profile
        """
        try:
            # Get the hash of the timewindow
            self.outputqueue.put('03|database|[DB]: Add_out_dstips called with profileid {}, twid {}, daddr_as_obj {}'.format(profileid, twid, str(daddr_as_obj)))
            hash_id = profileid + self.separator + twid
            data = self.r.hget(hash_id, 'DstIPs')
            if not data:
                data = {}
            try:
                # Convert the json str to a dictionary
                data = json.loads(data)
                # Add 1 because we found this ip again
                self.outputqueue.put('03|database|[DB]: Not the first time for this daddr. Add 1 to {}'.format(str(daddr_as_obj)))
                data[str(daddr_as_obj)] += 1
                data = json.dumps(data)
            except (TypeError, KeyError) as e:
                # There was no previous data stored in the DB
                self.outputqueue.put('03|database|[DB]: First time for this daddr. Make it 1 to {}'.format(str(daddr_as_obj)))
                data[str(daddr_as_obj)] = 1
                # Convet the dictionary to json
                data = json.dumps(data)
            # Store the dstips in the dB
            self.r.hset( profileid + self.separator + twid, 'DstIPs', str(data))
            # Mark the tw as modified
            self.markProfileTWAsModified(profileid, twid)
        except Exception as inst:
            self.outputqueue.put('01|database|[DB] Error in add_out_dstips in database.py')
            self.outputqueue.put('01|database|[DB] Type inst: {}'.format(type(inst)))
            self.outputqueue.put('01|database|[DB] Inst: {}'.format(inst))

    def add_out_tuple(self, profileid, twid, tupleid, data_tuple):
        """ Add the tuple going out for this profile """
        try:
            self.outputqueue.put('03|database|[DB]: Add_out_tuple called with profileid {}, twid {}, tupleid {}, data {}'.format(profileid, twid, tupleid, data_tuple))
            hash_id = profileid + self.separator + twid
            data = self.r.hget(hash_id, 'OutTuples')
            (symbol_to_add, previous_time, T2)  = data_tuple
            if not data:
                data = {}
            try:
                # Convert the json str to a dictionary
                data = json.loads(data)
                # Disasemble the input
                self.outputqueue.put('03|database|[DB]: Not the first time for tuple {}. Add the symbol: {}. Store previous_time: {}, T2: {}'.format(tupleid, symbol_to_add, previous_time, T2))
                # Get the last symbols of letters in the DB
                prev_symbols = data[tupleid][0]
                # Add it to form the string of letters
                new_symbol = prev_symbols + symbol_to_add
                # Bundle the data together
                new_data = (new_symbol, previous_time, T2)
                data[tupleid] = new_data
                self.outputqueue.put('04|database|[DB]: Letters so far for tuple {}: {}'.format(tupleid, new_symbol))
                data = json.dumps(data)
            except (TypeError, KeyError) as e:
                # There was no previous data stored in the DB
                self.outputqueue.put('03|database|[DB]: First time for tuple {}'.format(tupleid))
                new_data = (symbol_to_add, previous_time, T2)
                data[tupleid] = new_data
                # Convet the dictionary to json
                data = json.dumps(data)
            self.r.hset( profileid + self.separator + twid, 'OutTuples', str(data))
            # Mark the tw as modified
            self.markProfileTWAsModified(profileid, twid)
        except Exception as inst:
            self.outputqueue.put('01|database|[DB] Error in add_out_tuple in database.py')
            self.outputqueue.put('01|database|[DB] Type inst: {}'.format(type(inst)))
            self.outputqueue.put('01|database|[DB] Inst: {}'.format(inst))

    def getOutTuplesfromProfileTW(self, profileid, twid):
        """ Get the tuples """
        data = self.r.hget(profileid + self.separator + twid, 'OutTuples')
        return data

    def add_out_dstport(self, profileid, twid, dport):
        """ """
        pass

    def add_out_srcport(self, profileid, twid, sport):
        """ """
        pass

    def add_in_srcips(self, profileid, twid, saddr_as_obj):
        """
        Function if the flow is going in to the profile IP
        Add the srcip to this tw in this profile
        """
        try:
            # Get the hash of the timewindow
            hash_id = profileid + self.separator + twid
            data = self.r.hget(hash_id, 'SrcIPs')
            if not data:
                data = {}
            try:
                # Convert the json str to a dictionary
                data = json.loads(data)
                # Add 1 because we found this ip again
                data[str(saddr_as_obj)] += 1
                data = json.dumps(data)
            except (KeyError, TypeError) as e:
                data[str(saddr_as_obj)] = 1
                # Convet the dictionary to json
                data = json.dumps(data)
            self.r.hset( profileid + self.separator + twid, 'SrcIPs', str(data))
            # Mark the tw as modified
            self.markProfileTWAsModified(profileid, twid)
        except Exception as inst:
            self.outputqueue.put('01|database|[DB] Error in add_in_srcips in database.py')
            self.outputqueue.put('01|database|[DB] {}'.format(type(inst)))
            self.outputqueue.put('01|database|[DB] {}'.format(inst))
            self.outputqueue.put('01|database|[DB] Data after error: {}'.format(data))
            

    def add_in_dstport(self, profileid, twid, dport):
        """ """
        pass

    def add_in_srcport(self, profileid, twid, sport):
        """ """
        pass

    def add_srcips(self, profileid, twid, saddr):
        """ """
        pass

    def getFieldSeparator(self):
        """ Return the field separator """
        return self.separator

    def setEvidenceForTW(self, profileid, twid, data):
        """ Get the evidence for this TW for this Profile """
        data = {}
        data['Port Scan'] = [0.5, 1]
        data = json.dumps(data)
        self.r.hset(profileid + self.separator + twid, 'Evidence', str(data))

    def getEvidenceForTW(self, profileid, twid):
        """ Get the evidence for this TW for this Profile """
        data = self.r.hget(profileid + self.separator + twid, 'Evidence')
        return data





__database__ = Database()
