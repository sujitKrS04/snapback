import { useCallback, useEffect, useRef, useState } from "react";
import {
  Room,
  RoomEvent,
  Track,
  ConnectionState,
  type TrackPublication,
} from "livekit-client";

export interface TranscriptEntry {
  id: string;
  text: string;
  speaker: "user" | "agent";
  timestamp: number;
}

export interface UseLiveKitReturn {
  room: Room | null;
  connectionState: ConnectionState;
  isMicrophoneEnabled: boolean;
  connect: (token: string, url: string) => Promise<void>;
  disconnect: () => Promise<void>;
  toggleMicrophone: () => Promise<void>;
  transcripts: TranscriptEntry[];
  micTrack: MediaStreamTrack | null;
  agentTrack: MediaStreamTrack | null;
}

export function useLiveKit(): UseLiveKitReturn {
  const roomRef = useRef<Room | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [connectionState, setConnectionState] = useState<ConnectionState>(
    ConnectionState.Disconnected,
  );
  const [isMicrophoneEnabled, setIsMicrophoneEnabled] = useState(false);
  const [transcripts, setTranscripts] = useState<TranscriptEntry[]>([]);
  const [micTrack, setMicTrack] = useState<MediaStreamTrack | null>(null);
  const [agentTrack, setAgentTrack] = useState<MediaStreamTrack | null>(null);

  useEffect(() => {
    return () => {
      roomRef.current?.disconnect();
    };
  }, []);

  const connect = useCallback(async (token: string, url: string) => {
    if (roomRef.current) {
      await roomRef.current.disconnect();
    }

    const room = new Room({
      adaptiveStream: true,
      dynacast: true,
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    roomRef.current = room;
    setRoom(room);

    room.on(RoomEvent.Connected, () => {
      setConnectionState(ConnectionState.Connected);
      setIsMicrophoneEnabled(
        room.localParticipant?.isMicrophoneEnabled ?? false,
      );
      // Capture mic track after connection
      const pub = room.localParticipant?.getTrackPublication(
        Track.Source.Microphone,
      );
      if (pub?.track) {
        setMicTrack(pub.track.mediaStreamTrack);
      }
    });

    room.on(RoomEvent.Disconnected, () => {
      setConnectionState(ConnectionState.Disconnected);
      setIsMicrophoneEnabled(false);
      setMicTrack(null);
      setAgentTrack(null);
    });

    room.on(RoomEvent.ConnectionStateChanged, (state) => {
      setConnectionState(state);
    });

    room.on(RoomEvent.LocalTrackPublished, (pub: TrackPublication) => {
      if (pub.source === Track.Source.Microphone && pub.track) {
        setMicTrack(pub.track.mediaStreamTrack);
      }
    });

    room.on(RoomEvent.LocalTrackUnpublished, (pub: TrackPublication) => {
      if (pub.source === Track.Source.Microphone) {
        setMicTrack(null);
      }
    });

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        // Remove any existing agent audio elements to prevent duplicate playout and phase cancellation distortion
        document.querySelectorAll("audio[id^='agent-audio-']").forEach((el) => {
          (el as HTMLAudioElement).pause();
          el.remove();
        });
        const el = track.attach();
        el.id = `agent-audio-${track.sid}`;
        document.body.appendChild(el);
        // For agent TTS: capture remote audio track for analysis
        if (!track.isLocal) {
          setAgentTrack(track.mediaStreamTrack);
        }
      }
    });

    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        track.detach().forEach((el) => el.remove());
        document.querySelectorAll(`audio[id='agent-audio-${track.sid}']`).forEach((el) => {
          (el as HTMLAudioElement).pause();
          el.remove();
        });
        if (!track.isLocal) {
          setAgentTrack(null);
        }
      }
    });

    room.on(RoomEvent.DataReceived, (payload) => {
      try {
        const msg = JSON.parse(new TextDecoder().decode(payload));
        if (msg.type === "transcript" && msg.text) {
          setTranscripts((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              text: msg.text,
              speaker: msg.speaker === "agent" ? "agent" : "user",
              timestamp: Date.now(),
            },
          ]);
        }
      } catch {
        // Ignore non-JSON or malformed messages
      }
    });

    try {
      // Clean up any stale audio tags before connecting
      document.querySelectorAll("audio[id^='agent-audio-']").forEach((el) => {
        (el as HTMLAudioElement).pause();
        el.remove();
      });

      await room.connect(url, token);
      setConnectionState(ConnectionState.Connected);

      await room.localParticipant.setMicrophoneEnabled(true, {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      });
      setIsMicrophoneEnabled(true);
    } catch (err) {
      console.error("Failed to connect to LiveKit room:", err);
      setConnectionState(ConnectionState.Disconnected);
    }
  }, []);

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    if (room) {
      await room.localParticipant.setMicrophoneEnabled(false);
      await room.disconnect();
      roomRef.current = null;
      setRoom(null);
      setIsMicrophoneEnabled(false);
      setConnectionState(ConnectionState.Disconnected);
      setMicTrack(null);
      setAgentTrack(null);
      // Remove all agent audio playback elements from DOM
      document.querySelectorAll("audio[id^='agent-audio-']").forEach((el) => {
        (el as HTMLAudioElement).pause();
        el.remove();
      });
    }
  }, []);

  const toggleMicrophone = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;
    const enabled = !room.localParticipant.isMicrophoneEnabled;
    await room.localParticipant.setMicrophoneEnabled(enabled, {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
    setIsMicrophoneEnabled(enabled);
  }, []);

  return {
    room,
    connectionState,
    isMicrophoneEnabled,
    connect,
    disconnect,
    toggleMicrophone,
    transcripts,
    micTrack,
    agentTrack,
  };
}
