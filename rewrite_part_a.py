import time
from selenium import webdriver
from selenium.webdriver.common.by import By
import json
from urllib.parse import urlparse
# from geopy.geocoders import Here #EMFSMes4qAjPG6GIaFqtAt8DN_-Dh0KeqV-7zgdrmSU
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderUnavailable
from retrying import retry
from word2number import w2n
import re
import requests

start = time.time()

driver = webdriver.Chrome()
driver.get("https://schoolsearch.schools.nyc/")

# Locate the search button, click the search button so the website starts to fetch API info
search_button = driver.find_element(By.XPATH, '//input[@type="submit" and @class="btn btn-primary" and @value="Search"]')
search_button.click()
time.sleep(5)

# Now, we have the html content of elements inside the inner scroller
# insert all the info (school name, lat, lng, DOE url) into the dictionary of dictionaries
school_item_dict = {} #dict that stores all the info
no_url = set() # store a list of school that return None when trying to locate its school url, which means it doesn't has its own url attached
all_links = set() #get all links from the first layer
address_issue_schools = set() # get the name of all schools that have unformatted address that unable to convert to coordinates
school_name_issue_urls = set() #doe link is raise error of school name not found
google_url = set() #

def add_numeric_id(street):
    street_Ones = int(street) % 10
    print(street_Ones)
    if street_Ones == 0 or (int(street) % 100) in [11, 12, 13]:
        street = street + 'th'
    elif street_Ones == 1:
        street = street + 'st'
    elif street_Ones == 2:
        street = street + 'nd'
    elif street_Ones == 3:
        street = street + 'rd'
    else: 
        street = street + 'th'
    return street

def convert_word_to_numeric(word):
    try:
        word = word.strip().title()

        #ordinal conversion
        ordinal_mapping = {
            "First": "1st",
            "Second": "2nd",
            "Third": "3rd",
            "Fourth": "4th",
            "Fifth": "5th",
            "Sixth": "6th",
            "Seventh": "7th",
            "Eighth": "8th",
            "Ninth": "9th",
            "Tenth": "10th",
            "Eleventh": "11th",
            "Twelfth": "12th",
            "Thirteenth": "13th",
            "Fourteenth": "14th",
            "Fifteenth": "15th",
            "Sixteenth": "16th",
            "Seventeenth": "17th",
            "Eighteenth": "18th",
            "Nineteenth": "19th",
            "Twentieth": "20th",
            "Twenty-First": "21st",
            "Twenty-Second": "22nd",
            "Twenty-Third": "23rd",
            "Twenty-Fourth": "24th",
            "Twenty-Fifth": "25th",
        }
        if word.endswith(("st", "nd", "rd", "th")):
            if word in ordinal_mapping:
                return ordinal_mapping[word]
            else:
                return word  # Return the original word if not found in the mapping
        else:
            number = w2n.word_to_num(word)
            if number:
                number = add_numeric_id(str(number))
                return number
    except ValueError:
        return False

# function that format address and return coordinate pairs
def format_address(geolocator, address):
    # Case 1 : 511 7 Ave, Brooklyn, NY 11215, 8-21 Bay 25 Street, Queens, NY 11691  -- 7 Avenue to 7th Avenue, detect Bay as direction
    # Case 2 : 10 South Street, Slip 7, Manhattan, NY 10004                         -- make sure South is detected under streetname and 'Slip 7' is removed
    # add to case 1 - 2322 3 Avenue, Ground Floor, Manhattan, NY, 10035

    regex1 = r'''
        (?P<HouseNumber>[\w-]+)\s+                                                              # Matches '717 ' or '90-05'
        (?P<Direction>([news]|North|East|West|South|Bay|Brighton|Kings|Beach)?)\b\s*?           # Matches 'N ' or ' ' or 'North '
        (?P<StreetName>[0-9]+)\s*                                                               # Matches ONLY numeric
        (?P<StreetDesignator>Street|Avenue|Road|Lane|Drive|Walk|Blvd.|Court|Place|Terrace)\s*   # Matches 'Street ' or 'Avenue '
        ,\s+                                                                                    # Force a comma after the street
        (?: Ground\s+Floor,|(?:\w+\s*)?\d+(?:st|nd|rd|th|)\s+(?:\w+\s*)?)?                      # Remove " Ground Floor,"
        (?P<TownName>.*),\s+                                                                    # Matches 'MANKATO, '
        (?P<State>[A-Z]{2}),?\s+                                                                # Matches 'MN ' and 'MN, '
        (?P<ZIP>\d{5})                                                                          # Matches '56001'
    '''
    regex1 = re.compile(regex1, re.VERBOSE | re.IGNORECASE) #store all the set constriant in here, verbose and ignore case
    match1 = regex1.match(address) #store a match object that record detail info of the matched string, else None

    regex2 = r'''
        (?P<HouseNumber>[\w-]+)\s+                                                              # Matches '717 ' or '90-05'
        (?P<StreetName>[A-Za-z0-9\']+)\s*                                                       # Matches anything, later check if it is only numeric
        (?:\s+(Street|Avenue|Road|Lane|Drive|Walk|Blvd.|Court|Place|Terrace|Ave|Ave.|St))?      # Matches 'Street ' or 'Avenue '
        ,\s+                                                                                    # Force a comma after the street
        (?:Ground\s+Floor,|Aprt\s+\d+|Slip\s+\d+|Unit\s+\d+|Suite\s+\d+|Room\s+\d+|Shop\s+\d+|Office\s+\d+|Lot\s+\d+|Space\s+\d+|Bay\s+\d+|Box\s+\d+|(?:\w+\s*)?\d+(?:st|nd|rd|th|)\s+(?:\w+\s*)?)?
        # Not neccssary detail
        (?P<TownName>.*),\s+                                                                    # Matches 'MANKATO, '
        (?P<State>[A-Z]{2}),?\s+                                                                # Matches 'MN ' and 'MN, '
        (?P<ZIP>\d{5})                                                                          # Matches '56001'
    '''
    regex2 = re.compile(regex2, re.VERBOSE | re.IGNORECASE) #store all the set constriant in here, verbose and ignore case
    match2 = regex2.match(address)

    # address is incorrect
    if match1:
        street = match1.group('StreetName')
        street = add_numeric_id(street)
        direction = match1.group('Direction')
        # 133 Kings 1 Walk, Brooklyn, NY, 11233
        if direction == "Kings":
            direction = "Kingsborough"
        townname = match1.group('TownName')
        if townname == "Jamaica":
            townname = "Queens"
        address = match1.expand(fr'\g<HouseNumber> {direction} {street} \g<StreetDesignator>, {townname}, \g<State>, \g<ZIP>')
        print("After fixed: " + address)
        return address
    elif match2:
        street = match2.group('StreetName')
        if street.isdigit():
            street = add_numeric_id(street)
        
        # case where street number is written in word
        if convert_word_to_numeric(street) != False:
            street = convert_word_to_numeric(street)

        #Edge cases:
        # 285 Delancy Street, Manhattan, NY 10002
        if street == "Delancy":
            street = "Delancey"
        # 271 Seabreeze Avenue, Brooklyn, NY, 11224
        elif street == "Seabreeze":
            street = "Sea Breeze"
        # 83-78 Daniel Street, Queens, NY, 11435
        elif street == "Daniel":
            street = "Daniels"
        
        townname = match2.group('TownName')
        if townname == "Jamaica":
            townname = "Queens"
        street_designator = match2.group(3)

        housenumber = match2.group('HouseNumber')
        # run geocode to see if locaiton is none, if none check for house number inconsistance 
        # check if house number is xxx-xx, yes then remove -xx
        # 4360-78 Broadway, Manhattan, NY 10033             -- 4360-78 to 4360
        # 1962-84 Linden Blvd., Brooklyn, NY 11207
        location = geocode_with_retry(geolocator, address)
        if location == None:
            if '-' in housenumber:
                housenumber = housenumber.split('-')[0].strip()
                print("fixing round 2")
        if street_designator != None:
            address = match2.expand(fr'{housenumber} {street} {street_designator}, {townname}, \g<State>, \g<ZIP>')
        else:
            address = match2.expand(fr'{housenumber} {street}, {townname}, \g<State>, \g<ZIP>')
        print("After fixed2: " + address)
        return address
    else: 
        print("no match")
        return address
    
@retry(stop_max_attempt_number=5, wait_fixed=3000)
def geocode_with_retry(geolocator, location):
    try:
        return geolocator.geocode(location, timeout=10)
    except GeocoderUnavailable as e:
        print(f"GeocoderUnavailable: {e}")
        raise

# get each address and format address then store 
URL = "https://ws.schools.nyc/schooldata/GetSchools?search=&borough=&grade="
r = requests.get(url = URL)
data = r.json() # list of dictionories
driver.quit()

# In total there is 2900 schools
# loop through each school and store all the info:
# {'locationCode': 'K001', 'type': 'DOE', 'boroughName': 'Brooklyn', 'boroughCode': 'K', 'name': 'P.S. 001 The Bergen', 
# 'phoneNumber': '718-567-7661', 'primaryAddressLine': '309 47 STREET', 'zip': '11220', 'grades': 'PK,0K,01,02,03,04,05,SE', 
# 'stateCode': 'NY', 'x': '-8238913.43780000', 'y': '4960699.12320000', 'profile': '', 'neighborhood': 'Sunset Park West                                                           ', 
# 'district': '15', 'distance': '', 'dataflag': 'L'}
# 'profile' contains the school's private url link, need to turn lower case
avg_loop_runtime = 0
for school in data:
    each_school_time_avg = time.time()
    school_dict = school_item_dict.setdefault(school['name'].strip(), {})

    #coordinates
    address = school['primaryAddressLine'].lower() + ', ' + school['boroughName'] + ', ' + school['stateCode'] + ' ' + school['zip']
    geolocator = Nominatim(user_agent="my_request")
    address_x = address.strip()
    print(address_x)

    loc = format_address(geolocator, address_x)
    location = geocode_with_retry(geolocator, loc)
    if location != None:
        print("Got location: " + location.address + "\n")
        school_dict["Latitude"] = location.latitude #1st pair
        school_dict["Longitude"] = location.longitude  #2nd pair

    # Collect the name of all school where its address was not able to convert to coordinate
    else:
        school_dict["Latitude"] = "00000000000000000000000" #1st pair
        school_dict["Longitude"] = "0000000000000000000000000" #2nd pair
        address_issue_schools.add(school['name'].strip())
    
    # Grade store
    school_dict["Grade"] = school['grades']

    # District store
    school_dict["District"] = school['district']

    # Borough store
    school_dict["Borough"] = school['boroughName']
    
    # extract the school url using the exact path, if no school website is found, add the school name onto the no url list
    school_dict["School Website"] = school['profile'].lower()
    if school['profile'] != '':
        domain = urlparse(school['profile'].lower()).netloc.replace('www.', '').split('.')
        if domain == "google": #skip
            google_url.add(school['name'].strip())
            school_dict["Domain_1"] = ''
            school_dict["Domain_2"] = ''
            school_dict["Domain_3"] = ''
            school_dict["Domain_4"] = ''
        else:
            school_dict["Domain_1"] = domain[0] + ".org"
            school_dict["Domain_2"] = domain[0] + ".com"
            school_dict["Domain_3"] = domain[0] + ".edu"
            school_dict["Domain_4"] = domain[0] + ".net"
    else:
        no_url.add(school['name'].strip())
        school_dict["Domain_1"] = ''
        school_dict["Domain_2"] = ''
        school_dict["Domain_3"] = ''
        school_dict["Domain_4"] = ''
    
    end_each = time.time()
    each_school_time_avg = end_each - each_school_time_avg
    avg_loop_runtime += each_school_time_avg

print("\nThis is list of schools without url provided in the DOE website(no private school url attached): " + str(no_url))
print("\nDOE link issue: " + str(school_name_issue_urls))
print("\nNo coordinate get for address converting: " + str(address_issue_schools))
not_converted = len(address_issue_schools)
percentage = 100 - 100 * float(not_converted)/float(len(data))
no_url_number = len(no_url)
print("\nGeocode was not able to convert " + str(not_converted) + " school addresses.\nThus, the converting percentage is " + str(percentage) + "%.")
print("There are " + str(no_url_number) + " schools with no private url provided.")
avg_loop_runtime = avg_loop_runtime/len(data)
print(f"Average loop time: {avg_loop_runtime} seconds")

json_str = json.dumps(school_item_dict, indent= 4)
with open("data_as_json.json", "w") as outfile:
    outfile.write(json_str)
    print("\nSaved")

end = time.time()
runtime = end - start
print(f"Runtime of the program: {runtime} seconds")