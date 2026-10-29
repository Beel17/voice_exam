// Runs on the browser's audio thread: hands each block of microphone samples to the page,
// which downsamples and sends them only while the talk key is held.
class Capture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor("capture", Capture);
