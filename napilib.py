import requests, time

urlMain = 'https://api.notion.com/v1/'

class db:  # database object
    NOTION_URL = 'https://api.notion.com/v1/databases/'
    def req(self,oper, data, url):
        max_retries = 5
        wait_time = 0.01
        for attempt in range(max_retries):
            try:
                response = requests.request(
                    oper,
                    url,  # endpoint URL
                    headers={
                        "Authorization": f"Bearer {self.secret}",  # authorization
                        "Notion-Version": "2021-08-16",
                        "Content-Type": "application/json"
                    },
                    json=data  # json data
                )
                status = response.status_code
                if status != 200:
                    raise Exception(f"Error {status}: {response.text}")
                return response
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Error on attempt {attempt + 1} for {oper} request to {url}: {e}. Retrying in {wait_time:.1f} seconds...")
                    time.sleep(wait_time)
                    wait_time += 0.2
                else:
                    print(f"Failed after {max_retries} attempts: {e}")
    def __init__(self, secret, id):  # instantiation
        self.dbID = id  # its id(can be derived from its URL)
        self.secret = secret
    def add(self, row):  # append a row object to the actual database
        datad = row.getJson()  # get json data of the row object
        datad["parent"]["database_id"] = self.dbID  # specify target database in Json
        response = self.req("post", datad, urlMain + 'pages')
        page_id = response.json()["id"]  # Extract the page ID from the response
        return page_id  # Return the page ID

    def grab(self, filter=None, sort=None):  # retrieve latest data of the database
        if filter is None and sort is None:  # the filter and sort parameters are for future development
            self.data_j = self.req('post', {}, urlMain + 'databases/' + self.dbID + '/query').json()  # store the json file they responded into data_j(it would be converted into dict object automatically)
            self.parseTolist()  # parse data_j into a list of row object and save them

    def parseTolist(self):  # parse data_j into a list of row object and save them
        self.lrows = list()
        for i in range(len(self.data_j["results"])):  # for each entry
            self.lrows.append(row(raw=self.data_j["results"][i]))  # create a row object using json data and append it to lrows

class row:  # a object that represents a row (entry) for the database
    secret = None
    def __init__(self, **kwargs):
        if kwargs.get("raw"):  # if "raw" is specified, use it as the json data of the row
            self.data_d = kwargs["raw"]
        elif kwargs.get("id"):
            self.secret = kwargs.get('secret')
            self.data_d = self.req("get", {}, urlMain + 'pages/' + kwargs["id"]).json()
        else:
            self.data_d = {"parent": {}, "properties": {}}  # else construct empty json data
    def req(self,oper, data, url):
        max_retries = 5
        wait_time = 0.01
        for attempt in range(max_retries):
            try:
                response = requests.request(
                    oper,
                    url,  # endpoint URL
                    headers={
                        "Authorization": f"Bearer {self.secret}",  # authorization
                        "Notion-Version": "2021-08-16",
                        "Content-Type": "application/json"
                    },
                    json=data  # json data
                )
                status = response.status_code
                if status != 200:
                    raise Exception(f"Error {status}: {response.text}")        
                return response
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Error on attempt {attempt + 1} for {oper} request to {url}: {e}. Retrying in {wait_time:.1f} seconds...")
                    time.sleep(wait_time)
                    wait_time += 0.2
                else:
                    print(f"Failed after {max_retries} attempts: {e}")

    def getJson(self):
        return self.data_d

    def get(self, prop):  # retrieve a property value of the row using the property name
        prop_j = self.data_d["properties"][prop]  # location of the prop property
        ptype = prop_j["type"]  # access the type of prop
        # return the queried value from the correct spot in the json data according to its type
        if ptype == "title" or ptype == "rich_text":
            return prop_j[ptype][0]["text"]["content"]
        elif ptype == "number":
            return prop_j[ptype]
        elif ptype == "select":
            return prop_j[ptype]["name"]
        elif ptype == "multi_select":
            l = list()
            for x in prop_j[ptype]:
                l.append(x["name"])
            return l  # a list of all selected value
        elif ptype == "date":
            try:
                return prop_j[ptype]['start']  # iso format
            except:
                return ''
        elif ptype == "relation":
            return [row(id=x['id']) for x in prop_j[ptype]]

    def set(self, prop, val, ptype):  # set the value of a property
        # manipulate its json data according to the property name, value to be set with, and the type of the property
        if ptype == "title" or ptype == "rich_text":
            self.data_d["properties"].update({prop: {ptype: [{"text": {"content": val}}]}})
        elif ptype == "select":
            self.data_d["properties"].update({prop: {ptype: {"name": val}}})
        elif ptype == "date":
            self.data_d["properties"][prop] = {ptype: {"start": val}}
        elif ptype == "checkbox":
            self.data_d["properties"].update({prop: {ptype: val}})
        elif ptype == "people":
            self.data_d["properties"].update({prop: {ptype: [{"object": "user", "id": val}]}})
        elif ptype == "number":
            self.data_d["properties"].update({prop: {ptype: val}})

    def update(self):
        # Remove any keys that are not needed for the update request
        data_to_update = {"properties": self.data_d['properties']}
        # Logging for debugging purpose

        # Send the patch request with only the properties section of the data
        try:
            self.req('patch', data_to_update, 'https://api.notion.com/v1/pages/' + self.data_d["id"])
        except Exception as e:
            raise



    def clear(self, props):
        for prop in props:
            try:
                self.data_d["properties"].pop(prop)
            except:
                pass

    def dup(self):
        return row(raw=self.data_d.copy())

# class util:
#     def dup(row0, rel=None):
#         data = row0.getJson().copy()
#         data.pop("id")
#         return row(raw=req("post", data, urlMain + "pages").json())
