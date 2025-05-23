import sqlite3


class Database:
    def __init__(self, server, db_path: str = "portal_db.db"):
        self.server = server

        # ! Disabling check_same_thread fixes a lot of issues, but corruption could be a REALLY big problem, might need to check this.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cur = self.conn.cursor()

        # Create a table for servers
        # each server has an ID whcih won't have a duplicate, and it is the key
        # each server has a string name, which can't be null
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS servers (
            server_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        ''')

        # create a table of users
        # each user has a non-duplicate ID which is used as the key
        # each user has a username which can't be null
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_uuid TEXT PRIMARY KEY,
            username TEXT NOT NULL
        );
        ''')

        # create a table of memberships for each server
        # each membership links a user id to a server id
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS memberships (
            user_uuid TEXT,
            server_id INTEGER,
            PRIMARY KEY (user_uuid, server_id),
            FOREIGN KEY (user_uuid) REFERENCES users(user_uuid) ON DELETE CASCADE,
            FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE
        );
        ''')

        # create a table of channels
        # each channel has an id which cannot be a duplicate, and it is the key
        # each channel has a name which can't be null
        # each channel has a server id
        # each channel id references a server id
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            server_id INTEGER,
            FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE
        );
        ''')

        # create a table of messages
        # each message has an id which can't be a duplicate and is the key
        # each message has content
        # each message has a timestamp
        # each message has a user id
        # each message has a channel id
        # each message links the user id
        # each message links the channel id
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            message_id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            user_uuid TEXT,
            user_name TEXT,
            channel_id INTEGER,
            FOREIGN KEY (user_uuid) REFERENCES users(user_uuid) ON DELETE SET NULL,
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id) ON DELETE CASCADE
        );
        ''')

        # create a table of roles
        # each role has an id, which is the primary key
        # a name, which is text and can't be null
        # a rank, which tells the role if it is higher or lower than another role
        # and a list of permissions, either being 1 or 0 for True and False
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS roles (
            role_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rank INTEGER NOT NULL CHECK (rank >= 0 AND rank <= 255),
            send_messages INTEGER DEFAULT 1,
            view_message_history INTEGER DEFAULT 1,
            mute_members INTEGER DEFAULT 0,
            kick_members INTEGER DEFAULT 0,
            ban_members INTEGER DEFAULT 0,
            manage_channels INTEGER DEFAULT 0,
            manage_server INTEGER DEFAULT 0,
            super_admin INTEGER DEFAULT 0
        );
        ''')

        # create a table of all the roles each user has
        self.cur.execute('''
        CREATE TABLE IF NOT EXISTS user_roles (
            user_uuid TEXT,
            role_id INTEGER,
            server_id INTEGER,
            PRIMARY KEY (user_uuid, role_id, server_id),
            FOREIGN KEY (user_uuid) REFERENCES users(user_uuid) ON DELETE CASCADE,
            FOREIGN KEY (role_id) REFERENCES roles(role_id) ON DELETE CASCADE,
            FOREIGN KEY (server_id) REFERENCES servers(server_id) ON DELETE CASCADE
        );
        ''')

        server_id = self.get_server_by_name(self.server.server_info["title"])
        if not server_id:
            server_id = self.create_server(self.server.server_info["title"])
        else:
            server_id = server_id[0]

        channel_id = self.get_channel_by_name(server_id, "general")
        if not channel_id:
            channel_id = self.create_channel_in_server(server_id, "general")

        if not self.get_role_by_name("DefaultPerms"):
            self.create_role("DefaultPerms", 0, {})

        # create the system user, no one can have the same UUID
        if not self.user_exists("00000000-0000-0000-0000-000000000000"):
            self.create_user("SYSTEM", "00000000-0000-0000-0000-000000000000")
            
        self.commit()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def create_role(self, name: str, rank: int, permissions: dict[str, bool]) -> int:
        self.cur.execute('''
            INSERT INTO roles (name, rank,
                send_messages, view_message_history, mute_members,
                kick_members, ban_members, manage_channels, manage_server,
                super_admin
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            name, rank,
            int(permissions.get("send_messages", 1)),
            int(permissions.get("view_message_history", 1)),
            int(permissions.get("mute_members", 0)),
            int(permissions.get("kick_members", 0)),
            int(permissions.get("ban_members", 0)),
            int(permissions.get("manage_channels", 0)),
            int(permissions.get("manage_server", 0)),
            int(permissions.get("super_admin", 0))
        ))
        self.commit()
        return self.cur.lastrowid

    def assign_role_to_user(self, user_uuid: str, role_id: int, server_id: int) -> None:
        self.cur.execute('''
            INSERT OR IGNORE INTO user_roles (user_uuid, role_id, server_id)
            VALUES (?, ?, ?)
        ''', (user_uuid, role_id, server_id))
        self.server.log(f"Gave {user_uuid} role {role_id} in server {server_id}")
        self.commit()

    def get_roles_for_user_in_server(self, user_uuid: str, server_id: int):
        self.cur.execute('''
            SELECT r.* FROM roles r
            JOIN user_roles ur ON r.role_id = ur.role_id
            WHERE ur.user_uuid = ? AND ur.server_id = ?
        ''', (user_uuid, server_id))
        return self.cur.fetchall()
    
    def get_roles_with_users_in_server(self, server_id: int) -> list[tuple[str, tuple[str]]]:
        # Get all roles in the server and users with those roles
        self.cur.execute('''
            SELECT r.name, u.username
            FROM roles r
            JOIN user_roles ur ON r.role_id = ur.role_id
            JOIN users u ON ur.user_uuid = u.user_uuid
            WHERE ur.server_id = ?
            ORDER BY r.rank DESC, r.name ASC
        ''', (server_id,))

        rows = self.cur.fetchall()

        # Organize into {role_name: [usernames]}
        from collections import defaultdict
        role_map = defaultdict(list)
        for role_name, username in rows:
            role_map[role_name].append(username)

        # Return as list of tuples
        return list(role_map.items())
    
    def get_role_by_name(self, name: str):
        self.cur.execute("SELECT * FROM roles WHERE name = ? LIMIT 1", (name,))
        return self.cur.fetchone()
    
    def can_user(self, user_uuid: str, server_id: int, permission: str) -> bool:
        # Sanity check: permission must be a valid column in `roles`
        valid_permissions = {
            "send_messages",
            "view_message_history",
            "mute_members",
            "kick_members",
            "ban_members",
            "manage_channels",
            "manage_server",
            "super_admin"
        }
        if permission not in valid_permissions:
            raise ValueError(f"Invalid permission: {permission}")
        
        query = f'''
            SELECT MAX(r.{permission}) FROM roles r
            JOIN user_roles ur ON r.role_id = ur.role_id
            WHERE ur.user_uuid = ? AND ur.server_id = ?
        '''
        self.cur.execute(query, (user_uuid, server_id))
        result = self.cur.fetchone()

        return result and result[0] == 1

    def update_username(self, uuid: id, new_username: str) -> None:
        self.cur.execute("""
            UPDATE users SET username = ?
            WHERE user_uuid = ?
        """, (new_username, uuid))

    def get_channel_name_by_id(self, channel_id: int) -> str:
        self.cur.execute("""
            SELECT name FROM channels
            WHERE channel_id = ?
            LIMIT 1
        """, (channel_id,))
        result = self.cur.fetchone()
        return result[0] if result else None

    def get_server_from_channel(self, channel_id: int):
        self.cur.execute("""
            SELECT s.*
            FROM servers s
            JOIN channels c ON s.server_id = c.server_id
            WHERE c.channel_id = ?
            LIMIT 1
        """, (channel_id,))
        server = self.cur.fetchone()
        return server

    def get_user_by_name(self, username: str):
        self.cur.execute("""
            SELECT * FROM users
            WHERE username = ?
            LIMIT 1
        """, (username,))
        return self.cur.fetchone()
    
    def get_user(self, uuid: str):
        self.cur.execute("""
            SELECT * FROM users
            WHERE user_uuid = ?
            LIMIT 1
        """, (uuid,))
        return self.cur.fetchone()

    def get_server_by_name(self, server_name: str):
        self.cur.execute("""
            SELECT server_id, name FROM servers
            WHERE name = ?
            LIMIT 1
        """, (server_name,))
        return self.cur.fetchone()

    def get_channels_in_server(self, server_id: int):
        self.cur.execute("""
            SELECT channel_id, name FROM channels
            WHERE server_id = ?
        """, (server_id,))
        return self.cur.fetchall()
    
    def get_channels_by_server_name(self, server_name: str):
        self.cur.execute("""
            SELECT c.channel_id, c.name
            FROM channels c
            JOIN servers s ON c.server_id = s.server_id
            WHERE s.name = ?
        """, (server_name,))
        return self.cur.fetchall()
    
    def get_messages_in_channel(self, channel_id: int):
        self.cur.execute("""
            SELECT m.message_id, m.content, m.timestamp, u.username
            FROM messages m
            LEFT JOIN users u ON m.user_uuid = u.user_uuid
            WHERE m.channel_id = ?
            ORDER BY m.timestamp ASC
        """, (channel_id,))
        return self.cur.fetchall()
    
    def get_channel_by_name(self, server_id: int, channel_name: str):
        self.cur.execute("""
            SELECT * FROM channels
            WHERE server_id = ? AND name = ?
            LIMIT 1
        """, (server_id, channel_name))
        return self.cur.fetchone()
    
    def get_channel(self, server_id: int, channel_id: int):
        self.cur.execute("""
            SELECT * FROM channels
            WHERE server_id = ? AND channel_id = ?
            LIMIT 1
        """, (server_id, channel_id))
        return self.cur.fetchone()
    
    def create_channel_in_server(self, server_id: int, channel_name: str):
        if self.get_channel_by_name(server_id, channel_name):
            return

        # Check if the server exists
        self.cur.execute("SELECT 1 FROM servers WHERE server_id = ? LIMIT 1", (server_id,))
        if self.cur.fetchone() is None:
            raise ValueError(f"Server ID {server_id} does not exist.")

        # Insert the new channel
        self.cur.execute("""
            INSERT INTO channels (name, server_id)
            VALUES (?, ?)
        """, (channel_name, server_id))
        self.commit()

        return self.cur.lastrowid  # Return the new channel's ID
    
    def create_message_in_channel(self, channel_id: int, user_uuid: str, user_name: str, content: str):
        # Check if the channel exists
        self.cur.execute("SELECT 1 FROM channels WHERE channel_id = ? LIMIT 1", (channel_id,))
        if self.cur.fetchone() is None:
            raise ValueError(f"Channel ID {channel_id} does not exist.")

        # Optional: check if user exists (or let NULL be inserted)
        self.cur.execute("SELECT 1 FROM users WHERE user_uuid = ? LIMIT 1", (user_uuid,))
        if self.cur.fetchone() is None:
            raise ValueError(f"User UUID {user_uuid} does not exist.")

        # Insert the message
        self.cur.execute("""
            INSERT INTO messages (content, user_uuid, user_name, channel_id)
            VALUES (?, ?, ?, ?)
        """, (content, user_uuid, user_name, channel_id))
        self.commit()

        return self.cur.lastrowid  # Return the new message's ID

    def create_server(self, server_name: str):
        if self.get_server_by_name(server_name):
            raise ValueError("Server with that name already exists!")

        self.cur.execute("INSERT INTO servers (name) VALUES (?)", (server_name,))
        server_id = self.cur.lastrowid
        self.commit()
        return server_id
    
    def create_user(self, user_name: str, uuid: str):
        if self.user_exists(uuid):
            raise ValueError(f"A user with the name \"{user_name}\" already exists!")
        self.server.log(f"Creating user \"{user_name}\" with UUID \"{uuid}\"", 1)

        self.cur.execute("INSERT INTO users (user_uuid, username) VALUES (?, ?)", (uuid,user_name))
        user_uuid = self.cur.lastrowid
        self.commit()
        self.add_user_to_server(uuid, 1)
        return user_uuid
    
    def add_user_to_server(self, user_uuid: str, server_id: int):
        if self.is_user_in_server(user_uuid, server_id):
            raise ValueError("User is already in that server!")
        self.server.log(f"Adding user {user_uuid} to server {server_id}.", 1)

        memberships = [
            (user_uuid, server_id)
        ]
        result = self.cur.executemany("INSERT INTO memberships (user_uuid, server_id) VALUES (?, ?)", memberships)
        self.assign_role_to_user(user_uuid, 1, server_id)
        return result

    def user_exists(self, user_uuid: str):
        self.cur.execute("SELECT 1 FROM users WHERE user_uuid = ? LIMIT 1", (user_uuid,))
        return self.cur.fetchone() is not None

    def user_exists_by_name(self, username: str):
        self.cur.execute("SELECT 1 FROM users WHERE username = ? LIMIT 1", (username,))
        return self.cur.fetchone() is not None
    
    def server_exists(self, server_id: int):
        self.cur.execute("SELECT 1 FROM servers WHERE server_id = ? LIMIT 1", (server_id,))
        return self.cur.fetchone() is not None
    
    def server_exists_by_name(self, name: str):
        self.cur.execute("SELECT 1 FROM servers WHERE name = ? LIMIT 1", (name,))
        return self.cur.fetchone() is not None

    def is_user_in_server(self, user_uuid: str, server_id: int):
        self.cur.execute("""
            SELECT 1 FROM memberships
            WHERE user_uuid = ? AND server_id = ?
            LIMIT 1
        """, (user_uuid, server_id))
        return self.cur.fetchone() is not None

    def users_in_server(self, server_name: str):
        self.cur.execute("""
        SELECT u.username
        FROM users u
        JOIN memberships m ON u.user_uuid = m.user_uuid
        JOIN servers s ON m.server_id = s.server_id
        WHERE s.name = ?
        """, (server_name,))
        return [row[0] for row in self.cur.fetchall()]
    
    def users_in_server_id(self, server_id: int):
        self.cur.execute("""
            SELECT u.username
            FROM users u
            JOIN memberships m ON u.user_uuid = m.user_uuid
            WHERE m.server_id = ?
        """, (server_id,))
        return [row for row in self.cur.fetchall()]
    
    def servers_with_user(self, user_name: str):
        self.cur.execute("""
        SELECT s.name
        FROM servers s
        JOIN memberships m ON s.server_id = m.server_id
        JOIN users u ON m.user_uuid = u.user_uuid
        WHERE u.username = ?
        """, (user_name,))
        return [row[0] for row in self.cur.fetchall()]