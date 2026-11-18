"""LiveKit Cloud Room Service and restricted participant grants."""
from datetime import timedelta

TOKEN_TTL_SECONDS = 300


class LiveKitProvider:
    def __init__(self, http_url: str, api_key: str, api_secret: str):
        from livekit import api

        self.api = api
        self.http_url = http_url.rstrip('/')
        self.api_key = api_key
        self.api_secret = api_secret
        self.client = api.LiveKitAPI(self.http_url, api_key, api_secret)

    async def ensure_room(self, room_name: str) -> None:
        rooms = await self.client.room.list_rooms(self.api.ListRoomsRequest(names=[room_name]))
        if rooms.rooms:
            return
        try:
            await self.client.room.create_room(self.api.CreateRoomRequest(
                name=room_name, max_participants=2, empty_timeout=60, departure_timeout=20))
        except Exception:
            rooms = await self.client.room.list_rooms(self.api.ListRoomsRequest(names=[room_name]))
            if not rooms.rooms:
                raise

    async def room_exists(self, room_name: str) -> bool:
        rooms = await self.client.room.list_rooms(self.api.ListRoomsRequest(names=[room_name]))
        return bool(rooms.rooms)

    async def revoke_participant_token(self, room_name: str, identity: str, *, revoke_token_ts: int) -> None:
        admin_token = (self.api.AccessToken(self.api_key, self.api_secret)
                       .with_identity('tutor-room-service')
                       .with_ttl(timedelta(minutes=2))
                       .with_grants(self.api.VideoGrants(room_admin=True, room=room_name))
                       .to_jwt())
        url = f'{self.http_url}/twirp/livekit.RoomService/RemoveParticipant'
        async with self.client._session.post(
            url, json={'room': room_name, 'identity': identity, 'revoke_token_ts': revoke_token_ts},
            headers={'Authorization': f'Bearer {admin_token}'},
        ) as response:
            await response.read()
            if not 200 <= response.status < 300:
                raise RuntimeError('LiveKit participant revocation failed')

    async def delete_room(self, room_name: str) -> None:
        await self.client.room.delete_room(self.api.DeleteRoomRequest(room=room_name))

    def participant_token(self, room_name: str, identity: str, display_name: str) -> str:
        grants = self.api.VideoGrants(
            room_join=True, room=room_name, can_publish=True, can_subscribe=True,
            can_publish_data=False, can_publish_sources=['camera', 'microphone', 'screen_share'],
            room_admin=False, room_create=False, room_list=False, room_record=False,
            can_update_own_metadata=False)
        return (self.api.AccessToken(self.api_key, self.api_secret)
                .with_identity(identity).with_name(display_name)
                .with_ttl(timedelta(seconds=TOKEN_TTL_SECONDS))
                .with_grants(grants).to_jwt())

    async def close(self) -> None:
        await self.client.aclose()
